"""Speech setup: which half is missing, and the right instruction for it."""
import unittest
from unittest import mock

import speech_setup


class StatusTest(unittest.TestCase):
    def status(self, package_ok, model_ok):
        with mock.patch.object(speech_setup, '_package_available',
                               return_value=(package_ok, 'detail')), \
                mock.patch.object(speech_setup, '_model_available',
                                  return_value=(model_ok, 'detail')):
            return speech_setup.status()

    def test_both_missing_is_reported_as_two_separate_downloads(self):
        state = self.status(False, False)
        self.assertFalse(state['ready'])
        self.assertEqual(state['step'], 'both')
        self.assertIn('two downloads', state['message'].lower())

    def test_only_the_package_missing_says_so(self):
        state = self.status(False, True)
        self.assertEqual(state['step'], 'package')
        self.assertIn('package', state['message'].lower())
        self.assertNotIn('484', state['message'],
                         'a model download was mentioned when the model is present')

    def test_only_the_model_missing_names_its_size(self):
        state = self.status(True, False)
        self.assertEqual(state['step'], 'model')
        self.assertIn('484', state['message'])

    def test_ready_when_both_are_present(self):
        state = self.status(True, True)
        self.assertTrue(state['ready'])
        self.assertEqual(state['step'], '')
        self.assertEqual(state['message'], 'Local speech recognition is ready.')

    def test_nothing_is_claimed_to_be_online(self):
        self.assertTrue(self.status(True, True)['offline'])

    def test_installing_nothing_when_already_ready(self):
        with mock.patch.object(speech_setup, 'status',
                               return_value={'ready': True, 'step': ''}):
            result = speech_setup.start()
        self.assertFalse(result['started'])
        self.assertIn('already', result['reason'].lower())

    def test_an_unknown_step_is_refused(self):
        with mock.patch.object(speech_setup, 'status',
                               return_value={'ready': False, 'step': 'model'}):
            result = speech_setup.start('nonsense')
        self.assertFalse(result['started'])

    def test_auto_picks_the_step_that_is_actually_missing(self):
        with mock.patch.object(speech_setup, 'status',
                               return_value={'ready': False, 'step': 'package'}):
            with mock.patch.object(speech_setup.threading, 'Thread') as thread:
                result = speech_setup.start('auto')
        self.assertTrue(result['started'])
        self.assertEqual(result['kind'], 'package')
        thread.return_value.start.assert_called_once()


class MessageTest(unittest.TestCase):
    def test_the_reported_reason_tells_the_user_what_to_do(self):
        import bite_speech
        ready, reason = bite_speech.available()
        if ready:
            self.assertEqual(reason, '')
        else:
            self.assertTrue('install' in reason.lower() or 'tools/' in reason.lower(),
                            'the reason gives the user nothing to act on: ' + reason)


if __name__ == '__main__':
    unittest.main()
