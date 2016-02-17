#!/usr/bin/env python

from setuptools import setup, find_packages
import potter

setup(name='potter',
      version=potter.__version__,
      packages=find_packages(),
      entry_points={
          'console_scripts': [
              'potter = potter.cmd:main'
          ]
      },
      install_requires=[
          'docker-py >= 1.7',
          'pyyaml >= 3.0',
      ]
      )
