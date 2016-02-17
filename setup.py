#!/usr/bin/env python

from setuptools import setup, find_packages

setup(name='potter',
      version='0.2.0',
      packages=find_packages(),
      entry_points={
          'console_scripts': [
              'potter = potter:main'
          ]
      },
      install_requires=[
          'docker-py >= 1.7',
          'pyyaml >= 3.0',
      ]
      )
