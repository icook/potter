import tempfile
import os
import tarfile
import datetime
import time
import json
import sys
import logging
import docker
try:
    import jinja2
except ImportError:
    jinja2 = None

from . import wrapper

logger = logging.getLogger('potter')


class Step(object):
    def __init__(self, run, config, step_num):
        self.step_num = step_num
        self.run = run
        self.config = config

        self.start_time = time.time()
        self.cacheable = True
        self.labels = self.gen_labels()

    def gen_labels(self):
        return {
            "potter_repo": self.run.config['repo'],
            "potter_step": str(self.step_num),
            "potter_config_hash": self.config_hash,
            "potter_config": json.dumps(self.config)
        }

    def valid_cache(self, image):
        """ Check if this image is a valid cached version of this step """
        checks = [self._config_hash_changed, self._cache_disabled_flag, self._invalidation_timer_expired]
        for check in checks:
            if check(image) is False:
                return False

        return True

    def _config_hash_changed(self, image):
        if image.config_hash != self.config_hash:
            self.run.debug("Skipping {} cache because step configuration has changed"
                           .format(image))
            return False

    def _cache_disabled_flag(self, image):
        if self.config.get('nocache') is True:
            self.run.debug("Skipping {} cache because nocache flag".format(image))
            return False

    def _invalidation_timer_expired(self, image):
        invalidate_after = self.config.get('invalidate_after')
        if invalidate_after is not None:
            delta = datetime.timedelta(seconds=int(invalidate_after))
            if image.created < datetime.datetime.utcnow() - delta:
                self.run.debug("Skipping {} cache because cache image is too old."
                               .format(image))
                return False

    def execute(self, cached_images, target_image):
        if self.cacheable and cached_images:
            self.run.log("Found {} cached image(s) from previous run".format(len(cached_images)))
            cached_images = [i for i in cached_images if self.valid_cache(i)]
            # Use the most recently generated of valid cache images
            cached_images.sort(key=lambda img: img.created, reverse=True)
            if cached_images:
                image = cached_images[0]
                self.run.log("==> Using cached {}, saved {:.2f}".format(image, image.runtime), color="OKBLUE")
                return image

        return self._execute(target_image)

    def _execute(self, target_image):
        raise NotImplemented("_execute must be defined")

    def commit_container(self, container_id):
        assert len(container_id) == 64
        self.labels['potter_runtime'] = str(time.time() - self.start_time)
        resp = self.run.client.commit(container=container_id, conf={'Labels': self.labels},
                                      repository=self.run.config['repo'])
        self.run.client.remove_container(container=container_id)
        resp = self.run.client.inspect_image(image=resp['Id'])
        image = wrapper.Image.from_inspect(resp)
        self.run.log("==> New image {} generated in {}".format(image, image.runtime), color="OKGREEN")
        return image

    @property
    def config_hash(self):
        return str(hash(json.dumps(self.config)))


class Command(Step):
    def _execute(self, target_image):
        if isinstance(self.config['run'], list):
            command = self.config.get('join', " && ").join(self.config['run'])
        else:
            command = self.config['run']
        cmd = self.config.get('shell', '/bin/sh')
        container = self.run.client.create_container(image=target_image.id, command=[cmd, "-c", command])
        self.run.client.start(container['Id'])
        for log in self.run.client.attach(container=container['Id'], stdout=True, stderr=True, stream=True, logs=True):
            sys.stdout.write(log)
            sys.stdout.flush()
        if self.run.client.wait(container=container['Id']) != 0:
            raise Exception("Command step {} failed".format(self.step_num))
        return self.commit_container(container['Id'])


class Copy(Step):
    def _execute(self, target_image):
        container = self.run.client.create_container(image=target_image.id)
        uploadpath = os.path.join(self.config['dest'], os.path.basename(self.config['source']))
        self.run.log("Creating temporary tar file to upload {} to {}"
                     .format(self.config['source'], uploadpath))
        fo = tempfile.TemporaryFile()
        tar = tarfile.open(fileobj=fo, mode='w|')
        tar.add(self.config['source'], arcname=uploadpath)
        tar.close()

        def next_chunk(fo):
            total = float(fo.tell())
            fo.seek(0)
            read = 0
            while 1:
                data = fo.read(1024)
                if not data:
                    sys.stdout.write('\n')
                    break
                yield data
                read += 1024
                equals = int(read / total * 20)
                sys.stdout.write('\r[{}{}] {:.2f}%        '.format(
                    "=" * equals,
                    " " * (20 - equals),
                    read * 100 / total))
                sys.stdout.flush()
        self.run.log("Uploading and unpacking tar into container")
        self.run.client.put_archive(container=container['Id'], path='/', data=next_chunk(fo))
        fo.close()
        return self.commit_container(container['Id'])


class Pull(Step):
    def _execute(self, target_image):
        assert target_image is None  # Pull can only be first step
        tag = self.config.get('tag', 'latest')
        self.run.log("Pulling docker image {}:{}".format(self.config['image'], tag))
        progress = False
        for log in self.run.client.pull(repository=self.config['image'], tag=tag, stream=True):

            data = json.loads(log.decode('utf8'))
            if 'progress' in data:
                if progress:
                    sys.stdout.write('\r')
                sys.stdout.write(data['progress'])
                progress = True
            else:
                if progress:
                    progress = False
                    sys.stdout.write('\n')
                print(data['status'])
        self.start_time = time.time()  # Don't count the pull time as part of runtime
        self.run.log("==> Using image {}:{} as base".format(self.config['image'], tag), color="OKGREEN")
        container = self.run.client.create_container(image="{}:{}".format(self.config['image'], tag))
        return self.commit_container(container['Id'])
