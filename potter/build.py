import time
import os
import yaml
import logging
import docker
import docker.utils
try:
    import jinja2
except ImportError:
    jinja2 = None

from . import steps
from . import wrapper

logger = logging.getLogger('potter')


class Build(object):
    colors = dict(HEADER='\033[95m', OKBLUE='\033[94m', OKGREEN='\033[92m',
                  WARNING='\033[93m', FAIL='\033[91m')

    def __init__(self, context=None, **kwargs):
        self.use_color = True
        self.images = []
        self.containers = []
        self.__dict__.update(kwargs)
        config_contents = self.config_file.read().decode('utf8')
        if context is not None and jinja2 is None:
            raise Exception("Jinja2 must be installed to process context vars")
        elif jinja2 is not None:
            yml = jinja2.Template(config_contents).render(env=os.environ, **context or {})
        else:
            yml = config_contents
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
            image = wrapper.Image(resp, cache=True)
            unused_cache.add(image)
            cache_by_step.setdefault(image.step, []).append(image)

        return cache_by_step, unused_cache

    def create_steps(self):
        builtins = dict(pull=steps.Pull, command=steps.Command, copy=steps.Copy)
        step_objs = []
        for i, step in enumerate(self.build):
            typ = list(step.keys())[0]
            step_cls = builtins.get(typ)
            if step_cls is None:
                logger.error("{} is an invalid step type".format(typ))
                raise Exception()
            step_objs.append(step_cls(self, step[typ], i))
        return step_objs

    def run_steps(self, cache_by_step, unused_cache):
        cache_enabled = True
        target_image = None
        step_objs = self.create_steps()
        for i, step in enumerate(step_objs):
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

    def info(self):
        steps = self.create_steps()
        resps = self.client.images(all=True, filters={'label': "potter_repo={}".format(self.config['repo'])})
        self.log("All images related to {}".format(self.config['repo']), color='HEADER')
        self.log("{step:7}{short_id:15}{delta:20}{attrs:40}{config}"
                 .format(step="Step#", short_id="Image ID", delta="Created", config="Config", attrs="Attributes"))
        images = [wrapper.Image(r) for r in resps]
        images.sort(key=lambda i: i.step, reverse=True)
        for image in images:
            attrs = []
            if steps[image.step].valid_cache(image):
                attrs.append("Valid Cache")

            attrs = ", ".join(attrs)
            self.log("{s.step:<7}{s.short_id:15}{s.delta:20}{attrs:<40}{s.config}".format(attrs=attrs, s=image))

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
        images = [wrapper.Image(r) for r in resps]
        images.sort(key=lambda i: i.step, reverse=True)
        for image in images:
            self.log("{s.step:<7}{s.short_id:15}{s.delta:30}{s.config}".format(s=image))
            try:
                self.client.remove_image(image=image.id, force=True, noprune=True)
            except docker.errors.NotFound:
                pass
