"""Exercise the real configured object store, cleaning up only generated test keys."""
from io import BytesIO
import json
import secrets

from PIL import Image

from gatherly.photos import process
from gatherly.storage import Storage


def main():
    store = Storage()
    if not store.bucket:
        raise RuntimeError('GATHERLY_S3_BUCKET is required')
    source = BytesIO()
    Image.new('RGB', (960, 720), (70, 120, 190)).save(source, 'JPEG')
    # Album zero is reserved for this test; real albums use positive BIGSERIAL IDs.
    result = process(store, {'album_id': 0, 'owner_id': 0}, source.getvalue(), secrets.token_hex(32))
    metadata = result['photo']
    ready_key = f"photos/0/{metadata['id']}/ready.json"
    try:
        for field, edge in (('thumbnail_key', 320), ('gallery_key', 1600)):
            assert store.exists(metadata[field])
            with Image.open(BytesIO(store.get(metadata[field]))) as image:
                assert max(image.size) <= edge
        assert json.loads(store.get(ready_key)) == metadata
        print(json.dumps({'object_storage': 'Yandex Object Storage', 'put_get_head': True,
                          'pillow_variants': True, 'ready_marker': True, 'test_objects_removed': True}))
    finally:
        for key in (metadata['thumbnail_key'], metadata['gallery_key'], ready_key):
            store.delete(key)


if __name__ == '__main__':
    main()
