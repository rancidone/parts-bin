"""Initialize an operator-provisioned DSQL schema with IAM authentication."""
import argparse
from db.postgres.setup import initialize
from .connection import DSQLConfig


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--endpoint', required=True)
    parser.add_argument('--region', required=True)
    parser.add_argument('--user', default='admin')
    args = parser.parse_args()
    initialize(DSQLConfig(args.endpoint, args.region, args.user).connect)


if __name__ == '__main__':
    main()
