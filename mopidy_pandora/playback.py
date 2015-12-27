import logging
import time

from mopidy import backend

import requests

from mopidy_pandora import listener


logger = logging.getLogger(__name__)


class PandoraPlaybackProvider(backend.PlaybackProvider):
    SKIP_LIMIT = 5

    def __init__(self, audio, backend):
        super(PandoraPlaybackProvider, self).__init__(audio, backend)

        # TODO: It shouldn't be necessary to keep track of the number of tracks that have been skipped in the
        # player anymore once https://github.com/mopidy/mopidy/issues/1221 has been fixed.
        self._consecutive_track_skips = 0

        # TODO: add gapless playback when it is supported in Mopidy > 1.1
        # self.audio.set_about_to_finish_callback(self.callback).get()

        # def callback(self):
        # See: https://discuss.mopidy.com/t/has-the-gapless-playback-implementation-been-completed-yet/784/2
        # self.audio.set_uri(self.translate_uri(self.get_next_track())).get()

    def change_pandora_track(self, track):
        """ Attempt to retrieve the Pandora playlist item from the buffer and verify that it is ready to be played.

        A track is playable if it has been stored in the buffer, has a URL, and the header for the Pandora URL can be
        retrieved and the status code checked.

        :param track: the track to retrieve and check the Pandora playlist item for.
        :return: True if the track is playable, False otherwise.
        """
        try:
            pandora_track = self.backend.library.lookup_pandora_track(track.uri)
            if pandora_track.get_is_playable():
                # Success, reset track skip counter.
                self._consecutive_track_skips = 0
                self._trigger_track_changed(track)
            else:
                raise Unplayable("Track with URI '{}' is not playable.".format(track.uri))

        except (AttributeError, requests.exceptions.RequestException) as e:
            logger.warning('Error changing Pandora track: {}, ({})'.format(track), e)
            # Track is not playable.
            self._consecutive_track_skips += 1

            if self._consecutive_track_skips >= self.SKIP_LIMIT:
                raise MaxSkipLimitExceeded(('Maximum track skip limit ({:d}) exceeded.'
                                            .format(self.SKIP_LIMIT)))
            raise Unplayable("Cannot change to Pandora track '{}', ({}:{}).".format(track.uri,
                                                                                    type(e).__name__, e.args))

    def change_track(self, track):
        if track.uri is None:
            logger.warning("No URI for Pandora track '{}'. Track cannot be played.".format(track))
            return False

        try:
            self.change_pandora_track(track)
            return super(PandoraPlaybackProvider, self).change_track(track)

        except KeyError:
            logger.exception("Error changing Pandora track: failed to lookup '{}'.".format(track.uri))
            return False
        except Unplayable as e:
            logger.error(e)
            self.backend.prepare_next_track()
            return False
        except MaxSkipLimitExceeded as e:
            logger.error(e)
            self._trigger_skip_limit_exceeded()
            return False

    def translate_uri(self, uri):
        return self.backend.library.lookup_pandora_track(uri).audio_url

    def _trigger_track_changed(self, track):
        listener.PandoraPlaybackListener.send(listener.PandoraPlaybackListener.track_changed.__name__, track=track)

    def _trigger_skip_limit_exceeded(self):
        listener.PandoraPlaybackListener.send(listener.PandoraPlaybackListener.skip_limit_exceeded.__name__)


class EventHandlingPlaybackProvider(PandoraPlaybackProvider):
    def __init__(self, audio, backend):
        super(EventHandlingPlaybackProvider, self).__init__(audio, backend)

        self.double_click_interval = float(backend.config.get('double_click_interval'))
        self._click_time = 0

    def set_click_time(self, click_time=None):
        if click_time is None:
            self._click_time = time.time()
        else:
            self._click_time = click_time

    def get_click_time(self):
        return self._click_time

    def is_double_click(self):
        double_clicked = self._click_time > 0 and time.time() - self._click_time < self.double_click_interval

        if not double_clicked:
            self.set_click_time(0)

        return double_clicked

    def change_track(self, track):

        if self.is_double_click():
            self._trigger_doubleclicked()

        return super(EventHandlingPlaybackProvider, self).change_track(track)

    def resume(self):
        if self.is_double_click() and self.get_time_position() > 0:
            self._trigger_doubleclicked()

        return super(EventHandlingPlaybackProvider, self).resume()

    def pause(self):
        if self.get_time_position() > 0:
            self.set_click_time()

        return super(EventHandlingPlaybackProvider, self).pause()

    def _trigger_doubleclicked(self):
        self.set_click_time(0)
        listener.PandoraEventHandlingPlaybackListener.send(
            listener.PandoraEventHandlingPlaybackListener.doubleclicked.__name__)


class MaxSkipLimitExceeded(Exception):
    pass


class Unplayable(Exception):
    pass
