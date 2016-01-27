Potter
=============
Potter is an opinionated tool that creates container images. It aims to have
all functionality of `docker build`, but with several key improvments.

* Better caching controls to iterate faster when designing containers. Speed in general is a focus.
    * Specify a timeout in seconds afterwhich the layer will get rebuild
    * Specify a dependent set of files that trigger layer rebuild when changing
* Run multiple commands to create a single layer with nicer syntax
* Standard file format. Potter files are YAML, so integration with build systems is easier
* Better cleanup. Old unused images from previous builds are cleaned up when no longer needed.
* Pluggable architecture. Custom build step types can be imported dynamically at runtime.

That said, there are some features that Potter doesn't have right now that
Dockerfiles do.

* Support for `ADD`'s URL syntax, globbing, or tar importing
* Using both `ENTRYPOINT` and `CMD` to specify defaults to `ENTRYPOINT`.
* Using `WORKDIR` anywhere, as full paths are clearer

Features
========

Caches can have a timeout set

``` yml
config:
  repo: icook/testing
build:
  - pull:
    image: ubuntu
    tag: 14.04
    invalidate_after: 20
```

When run the first time, the image will be pulled or checked.

``` bash
==> Step 0 Pull cfg:{'invalidate_after': 20, 'image': 'ubuntu', 'tag': 14.04}
Pulling docker image ubuntu:14.04
Pulling from library/ubuntu
Digest: sha256:0844055d30c0cad5ac58097597a94640b0102f47d6fa972c94b7c129d87a44b7
Status: Image is up to date for ubuntu:14.04
==> Using image ubuntu:14.04 as base
==> New image <Image 1361ab73efbb> generated in 0.0156188011169
=====> Created image None in 1.88799595833
```

When run again quickly, it will use the cache:

``` bash
==> Step 0 Pull cfg:{'invalidate_after': 20, 'image': 'ubuntu', 'tag': 14.04}
Found 1 cached image(s) from previous run
==> Using cached <Image e2d3fc74b78b>, saved 0.01
=====> Created image <Image e2d3fc74b78b> in 0.0380079746246
```

Then after it expires in 20 seconds, the old cache will be invalidated and the
command will rerun.

``` bash
==> Step 0 Pull cfg:{'invalidate_after': 20, 'image': 'ubuntu', 'tag': 14.04}
Found 1 cached image(s) from previous run
Skipping <Image e2d3fc74b78b> cache because image is too old.
Pulling docker image ubuntu:14.04
Pulling from library/ubuntu
Digest: sha256:0844055d30c0cad5ac58097597a94640b0102f47d6fa972c94b7c129d87a44b7
Status: Image is up to date for ubuntu:14.04
==> Using image ubuntu:14.04 as base
==> New image <Image b6df2d2ba61b> generated in 0.0160322189331
=====> Created image <Image e2d3fc74b78b> in 2.5099029541
Removing unused cache image <Image e2d3fc74b78b>
```

Use
===

Potter build specs are similar in function to Dockerfiles, but the format is
different. While Dockerfiles are formatted as sequential commands, potter build
files separate building the image from configuring it.

**Build Commands**

In a Dockerfile

``` Dockerfile
FROM ubuntu:14.04

USER docker

RUN apt-get update && apt-get install -y locales && rm -rf /var/lib/apt/lists/* \
    && localedef -i en_US -c -f UTF-8 -A /usr/share/locale/locale.alias en_US.UTF-8

COPY . /projects
```

In a potter build file

``` yml
build:
  - pull:
      image: ubuntu
      tag: 14.04
  - command:
      run:
        - apot-get update
        - apt-get install -y locales
        - rm -rf /var/lib/apt/lists/*
        - localedef -i en_US -c -f UTF-8 -A /usr/share/locale/locale.alias en_US.UTF-8
      user: docker  # User is specified on each applicable build step, and does not apply to following steps
  - copy:
      source: .
      dest: /projects
      user: docker
```


**Config Commands**

In a Dockerfile

``` Dockerfile
ONBUILD rm -rf /
STOPSIGNAL 9
VOLUME /var/log /var/db
ENV key=val
EXPOSE 8080
EXPOSE 22 4365
LABEL key1=value1
LABEL key2=value2
MAINTAINER me@example.com
CMD /bin/bash
WORKDIR /home/docker
```

In a potter build file

``` yml
config:
  onbuild: rm -rf /
  stopsignal: 9
  volumes:
    "/var/log": /var/db
  env:
    key: val
  expose:
    8080:
    22: 4365
  labels:
    key1: value1
    key2: value2
  maintainer: me@example.com
  command: /bin/bash
```
