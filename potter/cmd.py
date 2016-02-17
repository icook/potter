import logging
import sys
import argparse

from . import build
from . import __version__


class StoreNameValuePair(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        if getattr(namespace, self.dest) is None:
            setattr(namespace, self.dest, {})
        n, v = values.split('=')
        getattr(namespace, self.dest)[n] = v


def main():
    parser = argparse.ArgumentParser(description='Build a docker container from a potter config file')
    parser.add_argument('config_file', help='the configuration file to load', type=argparse.FileType('r'))
    parser.add_argument('command', choices=['build', 'info'])
    parser.add_argument('--version', '-v', help='version information', action='store_true')
    parser.add_argument('--log-level', '-l', help='how verbose to log',
                        choices=['DEBUG', 'INFO', 'WARN', 'ERROR'], default='INFO')
    parser.add_argument('--context', help='key value pairs to feed to jinja in key=val format', action=StoreNameValuePair)
    args = parser.parse_args()

    console = logging.StreamHandler()
    console.setLevel(getattr(logging, args.log_level))
    formatter = logging.Formatter('%(message)s')
    console.setFormatter(formatter)
    logger = logging.getLogger('potter')
    logger.addHandler(console)
    logger.setLevel(getattr(logging, args.log_level))

    if args.version is True:
        print(__version__)
        return 0

    potter = build.Build(**vars(args))
    if args.command == 'build':
        potter.run()
    elif args.command == 'clean':
        potter.clean()
    elif args.command == 'info':
        potter.info()

    return 0

if __name__ == "__main__":
    sys.exit(main())
