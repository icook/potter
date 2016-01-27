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
