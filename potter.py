import yaml
import argparse
import tempfile
import os
import tarfile
import datetime
import time
import json
import sys
import logging
import docker
import docker.utils
import jinja2

logger = logging.getLogger('potter')


class Run(object):
    colors = dict(HEADER='\033[95m', OKBLUE='\033[94m', OKGREEN='\033[92m',
                  WARNING='\033[93m', FAIL='\033[91m')

    def __init__(self, context=None, **kwargs):
        self.use_color = True
        self.images = []
        self.containers = []
        self.__dict__.update(kwargs)
        yml = jinja2.Template(self.config_file.read()).render(env=os.environ, **context or {})
        config_unpacked = yaml.load(yml)
        self.config = config_unpacked['config']
        self.build = config_unpacked['build']
        self.client = docker.Client(**docker.utils.kwargs_from_env(assert_hostname=False))

    def log(self, msg, level=logging.INFO, color=None):
        if color and self.use_color:
            msg = "{}{}\033[0m".format(self.colors[color], msg)
        logger.log(level, msg)

    def debug(self, msg, color=None):
        return self.log(msg, level=logging.DEBUG, color=color)

    def run(self):
        try:
            start = time.time()
            cache_by_step, unused_cache = self.load_cache()
            target_image, unused_cache = self.run_steps(cache_by_step, unused_cache)
            self.remove_unused(unused_cache)
            self.log("=====> Created image {} in {}".format(target_image, time.time() - start), color='OKGREEN')
        except:
            logger.error("Exception raised, cleaning up ==========================")
            raise

    def load_cache(self):
        # Lookup all potential caching candidates
        resps = self.client.images(all=True, filters={'label': "potter_repo={}".format(self.config['repo'])})
        cache_by_step = {}
        unused_cache = set()
        for resp in resps:
            image = Image(resp, cache=True)
            unused_cache.add(image)
            cache_by_step.setdefault(image.step, []).append(image)

        return cache_by_step, unused_cache

    def create_steps(self):
        builtins = dict(pull=Pull, command=Command, copy=Copy)
        steps = []
        for i, step in enumerate(self.build):
            typ = list(step.keys())[0]
            step_cls = builtins.get(typ)
            if step_cls is None:
                logger.error("{} is an invalid step type".format(typ))
                raise Exception()
            steps.append(step_cls(self, step[typ], i))
        return steps

    def run_steps(self, cache_by_step, unused_cache):
        cache_enabled = True
        target_image = None
        steps = self.create_steps()
        for i, step in enumerate(steps):
            self.log("==> Step {} {} cfg:{}".format(
                i, step.__class__.__name__, step.config), color="HEADER")
            cache_objs = cache_by_step.get(i, []) if cache_enabled else []
            target_image = step.execute(cache_objs, target_image)
            # We've stopped using the cache, must disable moving forward
            if target_image.cache is False:
                cache_enabled = False
            else:
                unused_cache.remove(target_image)

        return target_image, unused_cache

    def remove_unused(self, unused_cache):
        for image in unused_cache:
            try:
                self.client.remove_image(image=image.id)
            except docker.errors.APIError:
                pass
            else:
                self.log("Removing unused cache image {}".format(image))

    def clean(self):
        resps = self.client.containers(all=True, filters={'label': "potter_repo={}".format(self.config['repo'])})
        self.log("Deleting all containers related to {}"
                 .format(self.config['repo']), color='HEADER')
        for resp in resps:
            self.log("Removing {}".format(resp['Id']))
            self.client.remove_container(container=resp['Id'], force=True)

        resps = self.client.images(all=True, filters={'label': "potter_repo={}".format(self.config['repo'])})
        self.log("Deleting all images related to {}"
                 .format(self.config['repo']), color='HEADER')
        images = [Image(r) for r in resps]
        images.sort(key=lambda i: i.step, reverse=True)
        for image in images:
            self.log(image.line_desc)
            try:
                self.client.remove_image(image=image.id, force=True, noprune=True)
            except docker.errors.NotFound:
                pass


class Image(object):
    """ A wrapper for images that potter has generated """
    def __init__(self, resp, cache=False):
        self.id = resp.pop('Id')
        self.created = datetime.datetime.utcfromtimestamp(resp.pop('Created', 0))
        self.extra = resp
        self.cache = cache  # Is this image cached?

        labels = {key[7:]: val for key, val in resp['Labels'].items() if key.startswith("potter_")}
        self.config = json.loads(labels.pop('config'))
        self.config_hash = labels.pop('config_hash')
        self.step = int(labels.pop('step'))
        self.runtime = float(labels.pop('runtime', 0))
        self.potter_labels = labels

        assert len(self.id) == 64

    @classmethod
    def from_inspect(cls, resp):
        new_resp = resp['Config']
        new_resp['Id'] = resp['Id']
        obj = cls(new_resp)
        without_milli = resp['Created'].rsplit(".", 1)[0]
        obj.created = datetime.datetime.strptime(without_milli, "%Y-%m-%dT%H:%M:%S")
        return obj

    @property
    def line_desc(self):
        return "{step}\t{id}\t{created}\t{config}".format(**self.__dict__)

    def __hash__(self):
        return int(self.id, 16)

    def __str__(self):
        return "<Image {}>".format(self.id[:12])


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
        image = Image.from_inspect(resp)
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

            data = json.loads(log)
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


def main():
    console = logging.StreamHandler()
    console.setLevel(logging.DEBUG)
    formatter = logging.Formatter('%(message)s')
    console.setFormatter(formatter)
    logger.addHandler(console)
    logger.setLevel(logging.DEBUG)

    class StoreNameValuePair(argparse.Action):
        def __call__(self, parser, namespace, values, option_string=None):
            if getattr(namespace, self.dest) is None:
                setattr(namespace, self.dest, {})
            n, v = values.split('=')
            getattr(namespace, self.dest)[n] = v

    parser = argparse.ArgumentParser(description='Build a docker container from a potter config file')
    parser.add_argument('config_file', help='the configuration file to load', type=argparse.FileType('r'))
    parser.add_argument('command', choices=['build', 'clean'])
    parser.add_argument('--context', help='key value pairs to feed to jinja', action=StoreNameValuePair)

    args = parser.parse_args()
    potter = Run(**vars(args))
    if args.command == 'build':
        potter.run()
    elif args.command == 'clean':
        potter.clean()

if __name__ == "__main__":
    main()
