"""Explicit subnets for the clean-install gate's Compose projects, so the gate never depends on Docker's automatic address pools (which a busy host can exhaust).

  compose_subnets.py validate CIDR CIDR CIDR      exit 0 when each is a private IPv4 network (prefix /24../29) and none overlap
  compose_subnets.py override CIDR                print a Compose override giving the default network that subnet
"""
import ipaddress
import sys


def validate(cidrs):
    nets = []
    for text in cidrs:
        net = ipaddress.ip_network(text, strict=True)
        if net.version != 4 or not net.is_private or not 24 <= net.prefixlen <= 29:
            raise ValueError(f'{text}: need a private IPv4 network between /24 and /29')
        for other in nets:
            if net.overlaps(other):
                raise ValueError(f'{net} overlaps {other}')
        nets.append(net)
    return nets


def override(cidr):
    net = validate([cidr])[0]
    return f'networks:\n  default:\n    ipam:\n      config:\n        - subnet: {net}\n'


if __name__ == '__main__':
    try:
        if sys.argv[1] == 'validate':
            validate(sys.argv[2:])
        elif sys.argv[1] == 'override':
            sys.stdout.write(override(sys.argv[2]))
        else:
            raise ValueError('unknown command')
    except (ValueError, IndexError) as e:
        print(f'compose_subnets: {e}', file=sys.stderr)
        sys.exit(2)
