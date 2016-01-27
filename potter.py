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

logger = logging.getLogger('potter')


class Run(object):
    colors = dict(HEADER='\033[95m', OKBLUE='\033[94m', OKGREEN='\033[92m',
                  WARNING='\033[93m', FAIL='\033[91m')

    def __init__(self, **kwargs):
        self.use_color = True
        self.images = []
        self.containers = []
        self.__dict__.update(kwargs)
        self.config = yaml.load(self.config_file.read())
        self.client = docker.Client(**docker.utils.kwargs_from_env(assert_hostname=False))

    def log(self, msg, level=logging.INFO, color=None):
        if color and self.use_color:
            msg = "{}{}\033[0m".format(self.colors[color], msg)
        logger.log(level, msg)

    def debug(self, msg, color=None):
        return self.log(msg, level=logging.DEBUG, color=color)

    def run(self):
        try:
            return self.run_steps()
        except:
            logger.error("Exception raised, cleaning up ==========================")
            raise

    def run_steps(self):
        start = time.time()

        builtins = dict(pull=Pull, command=Command, copy=Copy)
        image = None
        for i, step in enumerate(self.config['steps']):
            typ = step.pop('type')
            step_cls = builtins.get(typ)
            if step_cls is None:
                logger.error("{} is an invalid step type".format(typ))
                return False

            step_obj = step_cls(self, step, i, image)
            image = step_obj.execute()

        self.log("=====> Created {} in {}".format(image[:12], time.time() - start), color='OKGREEN')


class Step(object):
    def __init__(self, run, config, step_num, target_image):
        self.target_image = target_image
        self.step_num = step_num
        self.run = run
        self.config = config
        self.start_time = time.time()
        self.cacheable = True
        self.labels = self.gen_labels()

        self.run.log("==> Step {} {} cfg:{}".format(
            step_num, self.__class__.__name__, self.config), color="HEADER")

    def can_cache(self):
        """ Check if there is a cache, and if so, should we use it? """
        info = self.run.client.images(all=True, filters={'label': "potter-key={}".format(self.labels['potter-key'])})
        if not info:
            return False

        info = dict(id=info[0]['Id'],
                    config_hash=info[0]['Labels'].get('potter-config-hash'),
                    created=datetime.datetime.utcfromtimestamp(info[0]['Created']),
                    runtime=float(info[0]['Labels'].get('potter-runtime')))
        logger.info("Found cache for step. {}".format(info))
    def gen_labels(self):
        return {
            "potter_name": self.run.config['name'],
            "potter_step": str(self.step_num),
            "potter_config_hash": self.config_hash,
            "potter_config": json.dumps(self.config)
        }

        # Determine cache usage by step config
        if info['config_hash'] != self.labels['potter-config-hash']:
            logger.info("Skipping cache because step configuration has changed")
            return False

        if self.config.get('nocache') is True:
            logger.info("Skipping cache because nocache flag")
            return False

        invalidate_after = self.config.get('invalidate_after')
        if invalidate_after is not None:
            delta = datetime.timedelta(seconds=int(invalidate_after))
            if info['created'] < datetime.datetime.utcnow() - delta:
                logger.info("Skipping cache because cache image is too old.")
                return False

        return info

    def execute(self):
        if self.cacheable:
            info = self.can_cache()
            if info:
                self.run.log("==> Using cached image id {}".format(info['id'][:12]), color="OKBLUE")
                return info['id']

        return self._execute()

    def _execute(self):
        raise NotImplemented("_execute must be defined")

    def commit_container(self, container_id):
        assert len(container_id) == 64
        self.labels['potter-runtime'] = str(time.time() - self.start_time)
        image = self.run.client.commit(container=container_id, conf={'Labels': self.labels})
        assert len(image['Id']) == 64
        self.run.client.remove_container(container=container_id)
        self.run.log("==> New image with hash {} and labels {}".format(image['Id'][:12], self.labels), color="OKGREEN")
        return image['Id']


class Command(Step):
    def _execute(self):
        if isinstance(self.config['run'], list):
            command = self.config.get('join', " && ").join(self.config['run'])
        else:
            command = self.config['run']
        container = self.run.client.create_container(image=self.target_image, command=["/bin/bash", "-c", command])
        self.run.client.start(container['Id'])
        for log in self.run.client.attach(container=container['Id'], stdout=True, stderr=True, stream=True, logs=True):
            sys.stdout.write(log)
            sys.stdout.flush()
        return self.commit_container(container['Id'])


class Copy(Step):
    def _execute(self):
        container = self.run.client.create_container(image=self.target_image)
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
    def _execute(self):
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
        self.run.log("==> Using image {}:{} as base".format(self.config['image'], tag), color="OKGREEN")
        return self.config['image']


if __name__ == "__main__":
    console = logging.StreamHandler()
    console.setLevel(logging.DEBUG)
    formatter = logging.Formatter('%(message)s')
    console.setFormatter(formatter)
    logger.addHandler(console)
    logger.setLevel(logging.DEBUG)

    parser = argparse.ArgumentParser(description='Build a docker container from a potter config file')
    parser.add_argument('config_file', help='the configuration file to load', type=argparse.FileType('r'))

    potter = Run(**vars(parser.parse_args()))
    potter.run()
