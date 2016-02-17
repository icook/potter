import io
import datetime
import json
import potter as p


default_config = b"""
config:
  repo: icook/test
build:
    - pull:
          image: busybox
          tag: musl
"""


def make_run(config=default_config):
    fo = io.BytesIO(config)
    fo.seek(0)
    r = p.Run(config_file=fo)
    return r


def test_run():
    start = datetime.datetime.utcnow()
    r = make_run()
    im, unused_cache = r.run_steps({}, set())
    # Make sure no cache was used
    assert im.created > start
    assert im.cache is False
    assert im.step == 0
    assert im.potter_labels['repo'] == "icook/test"
    step_config = r.build[0]['pull']
    assert json.dumps(im.config) == json.dumps(step_config)
    r.clean()


def test_pull():
    r = make_run()
    im, unused_cache = r.run_steps({}, set())
    r.clean()


def test_command():
    r = make_run()
    step_config = {"run": ["touch /iwashere"]}
    r.build.append({"command": step_config})
    im, unused_cache = r.run_steps({}, set())
    r.clean()


def test_copy():
    r = make_run()
    r.build.append({"copy": {"source": ".", "dest": "/data"}})
    im, unused_cache = r.run_steps({}, set())
    r.clean()
