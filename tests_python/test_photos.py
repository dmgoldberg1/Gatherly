from io import BytesIO
import tempfile
import time
import unittest
from unittest.mock import patch

from PIL import Image

from gatherly import photos
from gatherly.service import ServiceError
from gatherly.storage import Storage


class PhotoProcessingTest(unittest.TestCase):
    def test_token_audience_expiry_album_binding_and_decoding_limits(self):
        secret = 'a' * 64
        claims = {'album_id': 7, 'owner_id': 3}
        token = photos.sign(secret, 'photo-upload', claims, 600)
        self.assertEqual(photos.verify(secret, 'photo-upload', token, 7)['owner_id'], 3)
        for purpose, aid, value in [('photo-read', 7, token), ('photo-upload', 8, token), ('photo-upload', 7, photos.sign(secret, 'photo-upload', claims, -1))]:
            with self.assertRaises(ServiceError):
                photos.verify(secret, purpose, value, aid)
        output = BytesIO()
        Image.new('RGB', (800, 600)).save(output, 'JPEG')
        with tempfile.TemporaryDirectory() as directory, patch.dict('os.environ', {'GATHERLY_STORAGE_DIR': directory, 'GATHERLY_S3_BUCKET': ''}):
            storage = Storage()
            with patch.object(photos, 'MAX_PIXELS', 100):
                with self.assertRaises(ServiceError) as error:
                    photos.process(storage, claims, output.getvalue(), secret)
                self.assertEqual(error.exception.status, 413)
            with self.assertRaises(ServiceError):
                photos.process(storage, claims, b'invalid-image', secret)
            ready = photos.process(storage, claims, output.getvalue(), secret)
            metadata = ready['photo']
            for key, size in [('thumbnail_key', 320), ('gallery_key', 1600)]:
                with Image.open(BytesIO(storage.get(metadata[key]))) as image:
                    self.assertLessEqual(max(image.size), size)
                    self.assertFalse(image.getexif())
            self.assertTrue(storage.exists(f"photos/7/{metadata['id']}/ready.json"))
