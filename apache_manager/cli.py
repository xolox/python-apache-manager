# Monitor and control Apache web server workers from Python.
#
# Author: Peter Odding <peter@peterodding.com>
# Last Change: March 29, 2018
# URL: https://apache-manager.readthedocs.io

"""
Usage: apache-manager [OPTIONS]

Command line interface to monitor the Apache web server and kill worker
processes that exceed resource thresholds. When no options are given the
server metrics and memory usage of workers are printed to the terminal.

Supported options:

  -w, --watch

    This option causes the Apache manager to redraw the collected metrics once
    every 10 seconds in a `top' like interface until interrupted using `q' (for
    quite) or Control-C.

  -a, --max-memory-active=SIZE

    Kill active Apache workers that are using more memory than specified by the
    SIZE argument. SIZE is expected to be a human readable memory size like 50K
    (50 kilobytes), 42M (42 megabytes), 2G (2 gigabytes), etc.

  -i, --max-memory-idle=SIZE

    Kill Apache workers that are using more memory than specified by the SIZE
    argument (see --max-memory-active for acceptable values of SIZE).

  -t, --max-ss, --max-time=TIMESPAN

    Kill Apache workers whose "time since the beginning of the most recent
    request" is greater than specified by the TIMESPAN argument. TIMESPAN is
    expected to be a human readable timespan like 2s (2 seconds), 3m (3
    minutes), 5h (5 hours), 2d (2 days), etc.

  -T, --hanging-worker-threshold=TIMESPAN

    Change the number of seconds before an active worker is considered hanging
    to TIMESPAN (see --max-time for acceptable values of TIMESPAN).

  -f, --data-file=PATH

    Change the pathname of the file where the Apache manager stores monitoring
    metrics after every run. Defaults to `/tmp/apache-manager.txt'.

  -z, --zabbix-discovery

    Generate a JSON fragment that's compatible with the low-level discovery
    support in the Zabbix monitoring system. With the right template in place
    this enables the Zabbix server to discover the names of the WSGI process
    groups that are active on any given server. This makes it possible to
    collect and analyze the memory usage of specific WSGI process groups.

  -n, --dry-run, --simulate

    Don't actually kill any Apache workers.

  -v, --verbose

    Increase verbosity (can be repeated).

  -q, --quiet

    Decrease verbosity (can be repeated).

  -h, --help

    Show this message and exit.
"""

# Standard library modules.
import getopt
import json
import logging
import sys

# External dependencies.
import coloredlogs
from humanfriendly import (
    format_size,
    format_timespan,
    parse_size,
    parse_timespan,
    pluralize,
)
from humanfriendly.terminal import (
    ansi_wrap,
    connected_to_terminal,
    HIGHLIGHT_COLOR,
    usage,
)

# Modules included in our package.
from apache_manager import ApacheManager, NATIVE_WORKERS_LABEL
from apache_manager.interactive import watch_metrics

# Initialize a logger for this program.
logger = logging.getLogger(__name__)


def main():
    """Command line interface for the ``apache-manager`` program."""
    # Configure logging output.
    coloredlogs.install(syslog=True)
    # Command line option defaults.
    data_file = '/tmp/apache-manager.txt'
    dry_run = False
    max_memory_active = None
    max_memory_idle = None
    max_ss = None
    watch = False
    zabbix_discovery = False
    verbosity = 0
    kw = dict()
    # Parse the command line options.
    try:
        options, arguments = getopt.getopt(sys.argv[1:], 'wa:i:t:T:f:znvqh', [
            'watch', 'max-memory-active=', 'max-memory-idle=', 'max-ss=',
            'max-time=', 'hanging-worker-threshold=', 'data-file=',
            'zabbix-discovery', 'dry-run', 'simulate', 'verbose', 'quiet',
            'help',
        ])
        for option, value in options:
            if option in ('-w', '--watch'):
                watch = True
            elif option in ('-a', '--max-memory-active'):
                max_memory_active = parse_size(value)
            elif option in ('-i', '--max-memory-idle'):
                max_memory_idle = parse_size(value)
            elif option in ('-t', '--max-ss', '--max-time'):
                max_ss = parse_timespan(value)
            elif option in ('-T', '--hanging-worker-threshold'):
                kw['hanging_worker_threshold'] = parse_timespan(value)
            elif option in ('-f', '--data-file'):
                data_file = value
            elif option in ('-z', '--zabbix-discovery'):
                zabbix_discovery = True
            elif option in ('-n', '--dry-run', '--simulate'):
                logger.info("Performing a dry run ..")
                dry_run = True
            elif option in ('-v', '--verbose'):
                coloredlogs.increase_verbosity()
                verbosity += 1
            elif option in ('-q', '--quiet'):
                coloredlogs.decrease_verbosity()
                verbosity -= 1
            elif option in ('-h', '--help'):
                usage(__doc__)
                return
    except Exception as e:
        sys.stderr.write("Error: %s!\n" % e)
        sys.exit(1)
    # Execute the requested action(s).
    manager = ApacheManager(**kw)
    try:
        if max_memory_active or max_memory_idle or max_ss:
            manager.kill_workers(
                max_memory_active=max_memory_active,
                max_memory_idle=max_memory_idle,
                timeout=max_ss,
                dry_run=dry_run,
            )
        elif watch and connected_to_terminal(sys.stdout):
            watch_metrics(manager)
        elif zabbix_discovery:
            report_zabbix_discovery(manager)
        elif data_file != '-' and verbosity >= 0:
            for line in report_metrics(manager):
                if line_is_heading(line):
                    line = ansi_wrap(line, color=HIGHLIGHT_COLOR)
                print(line)
    finally:
        if (not watch) and (data_file == '-' or not dry_run):
            manager.save_metrics(data_file)


def report_metrics(manager):
    """Create a textual summary of Apache web server metrics."""
    lines = ["Server metrics:"]
    for name, value in sorted(manager.server_metrics.items()):
        if name in ('total_traffic', 'bytes_per_second', 'bytes_per_request'):
            value = format_size(value)
        elif name == 'cpu_load':
            value = '%.1f%%' % value
        elif name == 'uptime':
            value = format_timespan(value)
        name = ' '.join(name.split('_'))
        name = name[0].upper() + name[1:]
        lines.append(" - %s: %s" % (name, value))
    main_label = "main Apache workers" if manager.wsgi_process_groups else "Apache workers"
    report_memory_usage(lines, main_label, manager.memory_usage)
    for name, memory_usage in sorted(manager.wsgi_process_groups.items()):
        report_memory_usage(lines, "WSGI process group '%s'" % name, memory_usage)
    return lines


def report_memory_usage(lines, label, memory_usage):
    """Create a textual summary of Apache worker memory usage."""
    lines.append("")
    workers = pluralize(len(memory_usage), "worker")
    lines.append("Memory usage of %s (%s):" % (label, workers))
    lines.append(" - Minimum: %s" % format_size(memory_usage.min))
    lines.append(" - Average: %s" % format_size(memory_usage.average))
    lines.append(" - Maximum: %s" % format_size(memory_usage.max))


def report_zabbix_discovery(manager):
    """Enable Zabbix low-level discovery of WSGI application groups."""
    worker_groups = [NATIVE_WORKERS_LABEL] + sorted(manager.wsgi_process_groups.keys())
    print(json.dumps({'data': [{'{#NAME}': name} for name in worker_groups]}))


def line_is_heading(line):
    """Check whether a line of output generated by :func:`report_metrics()` should be highlighted as a heading."""
    return line.endswith(':')
