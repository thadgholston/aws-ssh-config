#!/usr/bin/env python

import argparse
import boto3
import inflection
import re
from botocore.exceptions import ProfileNotFound

AMI_NAMES_TO_USER = {
    'amzn': 'ec2-user',
    'ubuntu': 'ubuntu',
    'CentOS': 'root',
    'DataStax': 'ubuntu',
    'CoreOS': 'core'
}

AMI_IDS_TO_USER = {
    'ami-ada2b6c4': 'ubuntu'
}

AMI_IDS_TO_KEY = {
    'ami-ada2b6c4': 'custom_key'
}

BLACKLISTED_REGIONS = [
    'cn-north-1',
    'us-gov-west-1'
]


def build_argument_parser():

    parser = argparse.ArgumentParser()

    parser.add_argument(
        '--default-user',
        help='Default ssh username to use if it can\'t'
        ' be detected from AMI name')

    parser.add_argument(
        '--keydir',
        default='~/.ssh/',
        help='Location of private keys')

    parser.add_argument(
        '--no-identities-only',
        action='store_true',
        help='Do not include IdentitiesOnly=yes in ssh config;'
        ' may cause connection refused if using ssh-agent')

    parser.add_argument(
        '--prefix',
        default='',
        help='Specify a prefix to prepend to all host names')

    parser.add_argument(
        '--private',
        action='store_true',
        help='Use private IP addresses (public are used by default)')

    parser.add_argument(
        '--profile',
        help='Specify AWS credential profile to use')

    parser.add_argument(
        '--ssh-key-name', default='', help='Override the ssh key to use')

    parser.add_argument(
        '--strict-hostkey-checking',
        action='store_true',
        help='Do not include StrictHostKeyChecking=no in ssh config')

    parser.add_argument(
        '--tags',
        action='append',
        default=[],
        help='A comma-separated list of tag names to be considered for '
        'concatenation. If omitted, all tags will be used.')

    parser.add_argument(
        '--user',
        help='Override the ssh username for all hosts')

    parser.add_argument(
        '--tag-filter',
        action='append',
        dest='tag_filters',
        default=[],
        help='tags to exclusively use for building config file')

    parser.add_argument(
        '--proxy-server-name',
        help='name of server to use a proxy')
    parser.add_argument(
        '--region',
        default='us-east-1',
        help='Which regions must be included. '
        'If omitted, all regions are considered')

    parser.add_argument(
        '--substitue',
        action='append',
        dest='words_to_substitue',
        default=[],
        help='word to substitue in tag value used to build host name')

    parser.add_argument(
        '--remove',
        action='append',
        dest='words_to_remove',
        default=[],
        help='word to remove from tag value used to build host name')

    return parser


def create_dict_of_filters(tag_and_values):
    tag, values, *rest = tag_and_values.split("=")
    values = values.split(",")
    name_of_tag = 'tag:%s' % (tag,)
    return {'Name': name_of_tag, 'Values': values}


def retreive_running_linux_instances(ec2, additional_filters=[]):
    required_filters = [{'Name': 'instance-state-name', 'Values': ['running']}]
    additional_filters = [
        create_dict_of_filters(tag_and_values)
        for tag_and_values in additional_filters
    ]
    filters = required_filters + additional_filters
    running_instances = ec2.instances.filter(Filters=filters)
    return [
        instance
        for instance in running_instances
        if instance.platform != 'windows'
    ]


def retrieve_proxy_server_by_name(ec2, name):
    name_tag_and_value = "Name=%s" % (name,)
    instances = retreive_running_linux_instances(ec2, [name_tag_and_value])
    return next(iter(instances), None)


def get_tag_map(instance):
    return {m['Key']: m['Value'] for m in instance.tags}


def generate_host_name(instance, hostname_tags,
                       substitution_list, remove_list, prefix):

    tag_map = get_tag_map(instance)

    tag_values_for_host_name = [tag_map[tag] for tag in hostname_tags]
    host_name = '-'.join(tag_values_for_host_name).lower()
    for word in remove_list:
        host_name = host_name.replace(word.lower(), '')
    for sub in substitution_list:
        split_sub = sub.split('=')
        original = split_sub[0].lower()
        replacement = split_sub[1].lower()
        host_name = host_name.replace(original, replacement)
    host_name = inflection.dasherize(host_name)
    host_name = re.sub('([0-9]+)', r'-\1', host_name)
    host_name = host_name.rstrip('-')
    if prefix:
        host_name = prefix + '-' + host_name

    return host_name


def generate_config_entry(instance, use_private_ip, no_identities_only,
                          strict_hostkey_checking, hostname_tags,
                          substitution_list, remove_list, prefix,
                          proxy_server=None):
    if use_private_ip and instance.private_ip_address:
        ip_address = instance.private_ip_address
    elif not use_private_ip and instance.public_ip_address:
        ip_address = instance.public_ip_address
    elif not use_private_ip and instance.private_ip_address:
        ip_address = instance.private_ip_address
    else:
        sys.stderr.write(
            'Cannot lookup ip address for instance %s' % instance.id)
        return

    host_name = generate_host_name(
        instance, hostname_tags, substitution_list, remove_list, prefix)

    if instance.id:
        print('# ID:', instance.id)
    print('Host', host_name)
    print('    HostName', ip_address)

    if proxy_server:
        print('    Proxycommand ssh ' + proxy_server + ' -W %h:%p')

    if not no_identities_only:
        # ensure ssh-agent keys don't flood when we know the right file to use
        print('    IdentitiesOnly yes')
    if not strict_hostkey_checking:
        print('    StrictHostKeyChecking no')
        print()


def main():

    parser = build_argument_parser()

    arguments = parser.parse_args()

    profile = arguments.profile

    if not profile:
        print("Missing profile")
        return

    region = arguments.region

    if not region:
        print("Missing region")
        return

    if not arguments.tags:
        print('Missing tags to use for HostName')
        return

    try:
        session = boto3.Session(region_name=region, profile_name=profile)
    except ProfileNotFound:
        print("Invalid profile provided: \"" + profile + "\".",
              "Check your ~/.aws/credentials file to confirm",
              "the name of your profile.")
        return

    ec2 = session.resource('ec2')

    filters = arguments.tag_filters

    instances = retreive_running_linux_instances(ec2, filters)

    proxy_server_name = arguments.proxy_server_name

    proxy_server = None

    if proxy_server_name:
        proxy_server = retrieve_proxy_server_by_name(ec2, proxy_server_name)

    if proxy_server_name and not proxy_server:
        print("Unable to find proxy server. Servers available are:")
        potential_proxy_servers = retreive_running_linux_instances(ec2)
        for instance in potential_proxy_servers:
            tag_map = get_tag_map(instance)
            if 'Name' in tag_map:
                print('  * ' + tag_map['Name'])
            else:
                print('  *',
                      instance.id,
                      '(ERROR retrieving name. Displaying instance id)')
        return

    if arguments.user:
        print('User ' + arguments.user)

    if arguments.keydir:
        keydir = arguments.keydir
    else:
        keydir = '~/.ssh/'

    if arguments.ssh_key_name:
        use_instance_key_name = False
        print('IdentityFile ' + keydir + arguments.ssh_key_name)
        print()

    substitution_list = arguments.words_to_substitue

    remove_list = arguments.words_to_remove

    hostname_tags = arguments.tags

    prefix = arguments.prefix

    use_private_ip = arguments.private

    no_identities_only = arguments.no_identities_only

    strict_hostkey_checking = arguments.strict_hostkey_checking

    if proxy_server:

        proxy_host_name = generate_host_name(
            proxy_server, hostname_tags, substitution_list,
            remove_list, prefix)
        generate_config_entry(
            proxy_server, use_private_ip, no_identities_only,
            strict_hostkey_checking, hostname_tags, substitution_list,
            remove_list, prefix)

    for instance in instances:
        generate_config_entry(
            instance, use_private_ip, no_identities_only,
            strict_hostkey_checking, hostname_tags,
            substitution_list, remove_list, prefix,
            proxy_host_name)


if __name__ == '__main__':
    main()
