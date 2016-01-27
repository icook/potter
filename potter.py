import yaml
import datetime
import time
import json
import tempfile
import sys
import sh
import logging
import docker
import docker.utils

logger = logging.getLogger('potter')


def run(*cmd, **kwargs):
    logger.debug("\033[95mRunning 'docker {}'\033[0m".format(" ".join(cmd)))
    output = ""
    try:
        for line in sh.docker(*cmd, _tty_in=True, _iter="out"):
            if len(output) < 4096:
                output += line
            sys.stdout.write(line)
            sys.stdout.flush()
    except sh.ErrorReturnCode:
        if kwargs.get('soft') is False:
            logger.info("\033[91mFailed processing {}\033[0m".format(cmd))
            raise

    return output.strip()


def main(images, containers):
    start = time.time()

    console = logging.StreamHandler()
    console.setLevel(logging.DEBUG)
    formatter = logging.Formatter('%(levelname)s: %(message)s')
    console.setFormatter(formatter)
    logger.addHandler(console)
    logger.setLevel(logging.DEBUG)

    client = docker.Client(**docker.utils.kwargs_from_env(assert_hostname=False))

    with open(sys.argv[1]) as f:
        config = yaml.load(f.read())

    image_name = ""
    for i, step in enumerate(config['steps']):
        typ = step.get('type')
        logger.info("==> Step {} {}".format(i, step))

        # Check if there is a cache, and if so, should we use it?
        start_step = time.time()
        labels = {"potter-key": '{}-{}'.format(config['name'], i),
                  "potter-config-hash": str(hash(json.dumps(step)))}
        info = client.images(all=True, filters={'label': "potter-key={}".format(labels['potter-key'])})
        if info:
            info = dict(id=info[0]['Id'],
                        config_hash=info[0]['Labels'].get('potter-config-hash'),
                        created=datetime.datetime.utcfromtimestamp(info[0]['Created']),
                        runtime=float(info[0]['Labels'].get('potter-runtime')))
            logger.info("Found cache for step. {}".format(info))

            # Determine cache usage by step config
            use_cache = True
            if info['config_hash'] != labels['potter-config-hash']:
                logger.info("Skipping cache because step configuration has changed")
                use_cache = False

            if step.get('nocache') is True:
                logger.info("Skipping cache because nocache flag")
                use_cache = False

            invalidate_after = step.get('invalidate_after')
            if invalidate_after is not None:
                delta = datetime.timedelta(seconds=int(invalidate_after))
                if info['created'] < datetime.datetime.utcnow() - delta:
                    logger.info("Skipping cache because cache image is too old.")
                    use_cache = False

            if use_cache:
                logger.info("Using cached image id {}".format(info['id']))
                image_name = info['id']
                continue


        container_name = None
        if typ == 'command':
            if isinstance(step['run'], list):
                command = "; ".join(step['run'])
            else:
                command = step['run']
            r = run("run", "-i", image_name, "/bin/bash", "-c", command)
            container_name = run("ps", "-l", "-q", "--no-trunc")
            containers.append(container_name)
        elif typ == 'pull':
            logger.info("Checking for docker image {}".format(step['image']))
            run('pull', step['image'])
            image_name = step['image']
            continue
        elif typ == 'copy':
            container_name = run("create", image_name)
            containers.append(container_name)
            r = run("cp", step['source'], "{}:{}".format(container_name, step['dest']))
        else:
            logger.error("{} is an invalid step type".format(typ))
            return

        if container_name is not None:
            assert len(container_name) == 64
            labels['potter-runtime'] = str(time.time() - start_step)
            ret = client.commit(container=container_name, conf={'Labels': labels})
            image_name = ret['Id']

        assert len(image_name) == 64
        images.append(image_name)
        logger.info("==> New image with hash {} and labels {}".format(image_name, labels))

        r = run("rm", container_name)
        containers.pop()

    final_image = images.pop()
    for image in images:
        run("rmi", image)
    logger.info("\033[93m==========> Created {} in {}\033[0m".format(final_image, time.time() - start))


if __name__ == "__main__":
    try:
        images = []
        containers = []
        main(images, containers)
    except:
        logger.error("Exception raised, cleaning up ==========================")
        for container in containers:
            run("rm", "-f", container, soft=True)
        for image in images:
            run("rmi", "-f", image, soft=True)
        raise
