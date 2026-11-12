"""Shared local volume or private Yandex Object Storage objects."""
import os
from pathlib import Path
import tempfile


class Storage:
    def __init__(self):
        self.bucket = os.getenv('GATHERLY_S3_BUCKET')
        self.root = Path(os.getenv('GATHERLY_STORAGE_DIR', '/data')).resolve()
        if self.bucket:
            import boto3
            from botocore.config import Config
            self.s3 = boto3.client('s3', endpoint_url=os.getenv('GATHERLY_S3_ENDPOINT', 'https://storage.yandexcloud.net'),
                                   region_name='ru-central1', config=Config(retries={'max_attempts': 2}, connect_timeout=3, read_timeout=10))

    def path(self, key):
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError('invalid object key')
        return path

    def put(self, key, content, content_type='application/octet-stream'):
        if self.bucket:
            self.s3.put_object(Bucket=self.bucket, Key=key, Body=content, ContentType=content_type)
        else:
            destination = self.path(key)
            destination.parent.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(dir=destination.parent)
            try:
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(name, destination)
            finally:
                if os.path.exists(name):
                    os.unlink(name)

    def get(self, key):
        if self.bucket:
            from botocore.exceptions import ClientError
            try:
                return self.s3.get_object(Bucket=self.bucket, Key=key)['Body'].read()
            except ClientError as exc:
                if exc.response['Error']['Code'] in ('404', 'NoSuchKey', 'NotFound'):
                    raise FileNotFoundError(key) from exc
                raise
        return self.path(key).read_bytes()

    def exists(self, key):
        if self.bucket:
            from botocore.exceptions import ClientError
            try:
                self.s3.head_object(Bucket=self.bucket, Key=key)
                return True
            except ClientError as exc:
                if exc.response['Error']['Code'] in ('404', 'NoSuchKey', 'NotFound'):
                    return False
                raise
        return self.path(key).is_file()

    def url(self, key):
        if self.bucket:
            return self.s3.generate_presigned_url('get_object', Params={'Bucket': self.bucket, 'Key': key}, ExpiresIn=300)
        return None


    def delete(self, key):
        if self.bucket:
            self.s3.delete_object(Bucket=self.bucket, Key=key)
        else:
            self.path(key).unlink(missing_ok=True)
