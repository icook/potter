import yaml
import tempfile
import os
import io
import tarfile
import datetime
import time
import json
import tempfile
import sys
import logging
import docker
import docker.utils

logger = logging.getLogger('potter')


client = docker.Client(**docker.utils.kwargs_from_env(assert_hostname=False))
def main(images, containers):
    start = time.time()

    console = logging.StreamHandler()
    console.setLevel(logging.DEBUG)
    formatter = logging.Formatter('%(levelname)s: %(message)s')
    console.setFormatter(formatter)
    logger.addHandler(console)
    logger.setLevel(logging.DEBUG)


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
            ret = client.create_container(image=image_name, command=["/bin/bash", "-c", command])
            client.start(ret['Id'])
            for log in client.attach(container=ret['Id'], stdout=True, stderr=True, stream=True, logs=True):
                sys.stdout.write(log)
                sys.stdout.flush()
            container_name = ret['Id']
            containers.append(container_name)
        elif typ == 'pull':
            logger.info("Checking for docker image {}".format(step['image']))
            progress = False
            for log in client.pull(repository=step['image'], tag=step.get('tag', 'latest'), stream=True):
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
            image_name = step['image']
            continue
        elif typ == 'copy':
            ret = client.create_container(image=image_name)
            container_name = ret['Id']
            containers.append(container_name)
            uploadpath = os.path.join(step['dest'], os.path.basename(step['source']))
            logger.error("Creating temporary tar file to upload {} to {}"
                         .format(step['source'], uploadpath))
            fo = tempfile.TemporaryFile()
            tar = tarfile.open(fileobj=fo, mode='w|')
            tar.add(step['source'], arcname=uploadpath)
            tar.close()
            def next_chunk(fo):
                total = float(fo.tell())
                # Make a newline that we'll rewrite a lot
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
            logger.error("Uploading and unpacking tar into container")
            client.put_archive(container=container_name, path='/', data=next_chunk(fo))
            fo.close()
        else:
            logger.error("{} is an invalid step type".format(typ))
            raise ValueError()

        if container_name is not None:
            assert len(container_name) == 64
            labels['potter-runtime'] = str(time.time() - start_step)
            ret = client.commit(container=container_name, conf={'Labels': labels})
            image_name = ret['Id']

        assert len(image_name) == 64
        logger.info("==> New image with hash {} and labels {}".format(image_name, labels))
        images.append(image_name)

        r = client.remove_container(container=container_name)
        containers.pop()

    final_image = images.pop()
    for image in images:
        client.remove_image(image=image)
    logger.info("\033[93m==========> Created {} in {}\033[0m".format(final_image, time.time() - start))


if __name__ == "__main__":
    try:
        images = []
        containers = []
        main(images, containers)
    except:
        logger.error("Exception raised, cleaning up ==========================")
        for container in containers:
            client.remove_container(container=container)
        for image in images:
            client.remove_image(image=image)
        raise
