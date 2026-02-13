import unittest
from unittest.mock import patch, MagicMock
from flask import Flask
from routes.tracking import tracking_bp

class TestTrackingAudio(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.register_blueprint(tracking_bp)
        self.client = self.app.test_client()
        self.app.config['SECRET_KEY'] = 'test'

    @patch('routes.tracking.TrackedDevice')
    @patch('routes.tracking.requests.get')
    def test_audio_stream_proxy(self, mock_get, mock_device_model):
        # Mock Device
        mock_device = MagicMock()
        mock_device.ip_address = '127.0.0.1'
        mock_device_model.query.filter_by.return_value.first.return_value = mock_device

        # Mock Upstream Response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.iter_content.return_value = [b'chunk1', b'chunk2']
        mock_get.return_value.__enter__.return_value = mock_response

        # Request
        response = self.client.get('/api/tracking/stream/audio/00:11:22:33:44:55')
        
        # Assertions
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, 'audio/x-raw')
        self.assertTrue(response.is_streamed)
        
        # Verify call to upstream
        mock_get.assert_called()
        args, kwargs = mock_get.call_args
        self.assertIn('http://127.0.0.1:5002/audio_stream.wav', args[0])
        self.assertTrue(kwargs.get('stream'))

    @patch('routes.tracking.TrackedDevice')
    @patch('routes.tracking.requests.get')
    def test_toggle_mic(self, mock_get, mock_device_model):
        # Mock Device
        mock_device = MagicMock()
        mock_device.ip_address = '127.0.0.1'
        mock_device_model.query.filter_by.return_value.first.return_value = mock_device

        # Mock Status Response (Active)
        mock_status_response = MagicMock()
        mock_status_response.status_code = 200
        mock_status_response.json.return_value = {'active': True}
        
        # Mock Stop Response
        mock_stop_response = MagicMock()
        mock_stop_response.status_code = 200
        
        mock_get.side_effect = [mock_status_response, mock_stop_response]

        # Request
        response = self.client.post('/api/tracking/toggle-mic/00:11:22:33:44:55')
        
        # Assertions
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['action'], 'stopped')

if __name__ == '__main__':
    unittest.main()
