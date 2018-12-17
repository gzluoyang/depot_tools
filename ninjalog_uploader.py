#!/usr/bin/env python
# Copyright 2018 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""
This is script to upload ninja_log from googler.

Server side implementation is in
https://cs.chromium.org/chromium/infra/go/src/infra/appengine/chromium_build_stats/

Uploaded ninjalog is stored in BigQuery table having following schema.
https://cs.chromium.org/chromium/infra/go/src/infra/appengine/chromium_build_stats/ninjaproto/ninjalog.proto

The log will be used to analyze user side build performance.
"""

import argparse
import cStringIO
import gzip
import json
import logging
import multiprocessing
import os
import platform
import socket
import subprocess
import sys

from third_party import httplib2

# These build configs affect build performance a lot.
WHITELISTED_CONFIGS = [
    'symbol_level', 'use_goma', 'is_debug', 'is_component_build', 'enable_nacl',
    'host_os', 'host_cpu', 'target_os', 'target_cpu'
]

def IsGoogler(server):
    """Check whether this script run inside corp network."""
    try:
        h = httplib2.Http()
        _, content = h.request('https://'+server+'/should-upload', 'GET')
        return content == 'Success'
    except httplib2.HttpLib2Error:
        return False

def ParseGNArgs(gn_args):
    """Parse gn_args as json and return config dictionary containining
    whitelisted configs.

    >>> ParseGNArgs("[]")
    {}
    >>> ParseGNArgs('[{\
    "current": {"value": "true"}, \
    "default": {"value": "false"}, \
    "name": "is_component_build"}, \
    {\
    "current": {"value": "true"}, \
    "default": {"value": "false"}, \
    "name": "something_secret"}]')
    {u'is_component_build': u'true'}
    """
    configs = json.loads(gn_args)
    build_configs = {}

    for config in configs:
        key = config["name"]
        if key not in WHITELISTED_CONFIGS:
            continue
        build_configs[key] = config["current"]["value"]

    return build_configs

def RemoveBuildDir(cmdline):
    """Remove build directory (-C) from cmdline that may contain PII.

    >>> RemoveBuildDir(['ninja','-C', 'out/Release'])
    ['ninja']
    >>> RemoveBuildDir(['ninja'])
    ['ninja']
    >>> RemoveBuildDir(['ninja', '-C'])
    ['ninja']
    >>> RemoveBuildDir(['ninja', '-Cout/Relese'])
    ['ninja']
    """

    new_cmdline = []

    idx = 0
    while idx < len(cmdline):
        if cmdline[idx] == '-C':
            idx += 2
            continue
        if cmdline[idx].startswith('-C'):
            idx += 1
            continue
        new_cmdline.append(cmdline[idx])
        idx += 1

    return new_cmdline

def GetMetadata(cmdline, ninjalog):
    """Get metadata for uploaded ninjalog."""

    build_dir = os.path.dirname(ninjalog)

    build_configs = {}

    try:
        args = ['gn', 'args', build_dir, '--list', '--short', '--json']
        if sys.platform == 'win32':
            # gn in PATH is bat file in windows environment (except cygwin).
            args = ['cmd', '/c'] + args

        gn_args = subprocess.check_output(args)
        build_configs = ParseGNArgs(gn_args)
    except subprocess.CalledProcessError as e:
        logging.error("Failed to call gn %s", e)
        build_configs = {}

    # Stringify config.
    for k in build_configs:
        build_configs[k] = str(build_configs[k])

    metadata = {
        'platform': platform.system(),
        'cpu_core': multiprocessing.cpu_count(),
        'build_configs': build_configs,
        'cmdline': RemoveBuildDir(cmdline),
    }

    jflag = GetJflag(cmdline)
    if jflag is not None:
        metadata['jflag'] = jflag

    return metadata

def GetNinjalog(cmdline):
    """GetNinjalog returns the path to ninjalog from cmdline.

    >>> GetNinjalog(['ninja'])
    './.ninja_log'
    >>> GetNinjalog(['ninja', '-C', 'out/Release'])
    'out/Release/.ninja_log'
    >>> GetNinjalog(['ninja', '-Cout/Release'])
    'out/Release/.ninja_log'
    >>> GetNinjalog(['ninja', '-C'])
    './.ninja_log'
    >>> GetNinjalog(['ninja', '-C', 'out/Release', '-C', 'out/Debug'])
    'out/Debug/.ninja_log'
    """
    # ninjalog is in current working directory by default.
    ninjalog_dir = '.'

    i = 0
    while i < len(cmdline):
        cmd = cmdline[i]
        i += 1
        if cmd == '-C' and i < len(cmdline):
            ninjalog_dir = cmdline[i]
            i += 1
            continue

        if cmd.startswith('-C') and len(cmd) > len('-C'):
            ninjalog_dir = cmd[len('-C'):]

    return os.path.join(ninjalog_dir, '.ninja_log')

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--server',
                        default='chromium-build-stats.appspot.com',
                        help='server to upload ninjalog file.')
    parser.add_argument('--ninjalog', help='ninjalog file to upload.')
    parser.add_argument('--verbose', action='store_true',
                        help='Enable verbose logging.')
    parser.add_argument('--cmdline', required=True, nargs=argparse.REMAINDER,
                        help='command line args passed to ninja.')

    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.INFO)
    else:
        # Disable logging.
        logging.disable(logging.CRITICAL)

    if not IsGoogler(args.server):
        return 0


    ninjalog = args.ninjalog or GetNinjalog(args.cmdline)
    if not os.path.isfile(ninjalog):
        logging.warn("ninjalog is not found in %s", ninjalog)
        return 1

    output = cStringIO.StringIO()

    with open(ninjalog) as f:
        with gzip.GzipFile(fileobj=output, mode='wb') as g:
            g.write(f.read())
            g.write('# end of ninja log\n')

            metadata = GetMetadata(args.cmdline, ninjalog)
            logging.info('send metadata: %s', metadata)
            g.write(json.dumps(metadata))

    h = httplib2.Http()
    resp_headers, content = h.request(
        'https://'+args.server+'/upload_ninja_log/', 'POST',
        body=output.getvalue(), headers={'Content-Encoding': 'gzip'})

    if resp_headers.status != 200:
        logging.warn("unexpected status code for response: %s",
                     resp_headers.status)
        return 1

    logging.info('response header: %s', resp_headers)
    logging.info('response content: %s', content)
    return 0

if __name__ == '__main__':
    sys.exit(main())
