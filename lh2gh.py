#!/usr/bin/env python
"""Convert issues exported from Lighthouse to the `GitHub import format`__

.. __: https://gist.github.com/izuzak/654612901803d0d0bc3f

"""

import re
import os
import sys
import json
from itertools import count
from collections import namedtuple

import logging
logger = logging.getLogger()
logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)s: %(message)s')


opt = None

def main():
    global opt
    opt = parse_cmdline()

    check_dest()

    lhms = read_milestones()
    ghms = convert_milestones(lhms)

    lhs = read_tickets()
    ghs = convert_tickets(lhs, ghms)

    save_tickets(ghs)
    save_milestones(ghms)

def check_dest():
    dir = opt.destdir
    if not os.path.exists(dir):
        return
    if os.path.isdir(dir):
        if not os.listdir(dir):
            return
    raise ScriptError("DESTDIR should be non existing or an empty dir")

def map_user(name):
    return opt.usermap.get(name, opt.fallback_user or name)

def map_ticket_id(n):
    """Return the new id for a ticket."""
    if opt.remap_until is None:
        return n
    return n + opt.remap_offset if n <= opt.remap_until else n

def fix_tickets_numbers(s):
    """Apply the tickets map to the tickets numbers found a string."""
    return re.sub(
        '#(\d+)', lambda m: '#%d' % map_ticket_id(int(m.group(1))), s)

def fix_code_blocks(s):
    """Convert the Lighthouse '@@@ lang' into Github markdown"""
    return re.sub(r'(?m)^@@@\s*([\r\n]*)$', r'```\1', s)


def read_tickets():
    """Return a map ticket id -> json with the Lighthouse tickets"""
    tdirs = os.listdir(os.path.join(opt.srcdir, 'tickets'))
    rv = {}
    for tdir in tdirs:
        logger.debug('importing %s' % tdir)
        data = json.load(open(
            os.path.join(opt.srcdir, 'tickets', tdir, 'ticket.json')))
        if data:
            rv[int(tdir.split('-', 1)[0])] = data
    return rv

GithubTicket = namedtuple('GithubTicket', 'ticket comments')

def convert_tickets(lhs, milestones):
    ghs = {}
    for n, lh in lhs.iteritems():
        t = convert_ticket(lh, milestones)
        if t:
            ghs[map_ticket_id(n)] = t
    return ghs

def convert_ticket(lh, milestones):
    lh = lh['ticket']
    if lh['spam']:
        logger.info("ignoring spam ticket %s - %s",
                    lh['number'], lh['title'])
        return None

    gh = {}
    comments = []

    gh['number'] = map_ticket_id(lh['number'])

    body = lh['latest_body']
    m = re.match(r'(?s)^Submitted by:\s+([^\n]+)\n\n(.*)', body)
    if m:
        # submitted through the website
        author = m.group(1)
        body = m.group(2).lstrip()
    else:
        author = lh['creator_name']

    parts = []

    user = map_user(author)
    if user == opt.fallback_user:
        parts.append("Originally submitted by: %s" % author)

    if lh['number'] != gh['number']:
        # TODO: add command line option for this URL
        parts.append(
            "Originally submitted as number %d - "
            "http://psycopg.lighthouseapp.com/projects/62710/tickets/%d"
                 % (lh['number'], lh['number']))

    parts.append(body)
    body = '\n\n'.join(parts)
    gh['body'] = fix_code_blocks(fix_tickets_numbers(body))

    gh['title'] = lh['title']
    gh['created_at'] = lh['created_at']
    gh['updated_at'] = lh['updated_at']
    gh['user'] = user
    gh['state'] = 'closed' if lh['closed'] else 'open'
    if 'assigned_user_name' in lh:
        gh['assignee'] = map_user(lh['assigned_user_name'])

    if lh['milestone_id']:
        gh['milestone'] = milestones[lh['milestone_id']]['number']

    # let's not spam the label
    # if lh['tag']:
    #     gh['labels'] = [a + b
    #         for (a, b) in re.findall(
    #             r'(?:([^"\s][^\s]*))|(?:"([^"]*)")', lh['tag'])]

    gh['labels'] = []
    label = {
        'open': 'confirmed',
        'hold': 'hold',
        'invalid': 'invalid',
    }.get(lh['state'])

    if label:
        gh['labels'].append(label)

    if 'feature' in (lh['tag'] or ''):
        gh['labels'].append('enhancement')

    if 'question' in (lh['tag'] or ''):
        gh['labels'].append('question')

    # get the closed date from the first closed version
    for ver in lh['versions']:
        if ver['closed']:
            gh['closed_at'] = ver['created_at']

    for ver in lh['versions'][1:]:
        if ver['body']:
            comments.append(convert_comment(ver))

    return GithubTicket(gh, comments)

def convert_comment(ver):
    c = {}
    c['body'] = fix_code_blocks(fix_tickets_numbers(ver['body']))
    c['user'] = map_user(ver['user_name'])
    if c['user'] == opt.fallback_user:
        c['body'] = "Originally submitted by: %s\n\n%s" % (
            ver['user_name'], c['body'])
    c['created_at'] = ver['created_at']
    c['updated_at'] = ver['updated_at']
    return c

def save_tickets(ghs):
    dir = os.path.join(opt.destdir, 'issues')
    if not os.path.exists(dir):
        os.makedirs(dir)
    for gh in ghs.itervalues():
        save_ticket(dir, gh)

def save_ticket(dir, gh):
    logger.info('saving ticket %s - %s',
                gh.ticket['number'], gh.ticket['title'][:40])
    fn = os.path.join(dir, '%d.json' % gh.ticket['number'])
    with open(fn, 'w') as f:
        json.dump(gh.ticket, f)

    if gh.comments:
        fn = os.path.join(dir, '%d.comments.json' % gh.ticket['number'])
        with open(fn, 'w') as f:
            json.dump(gh.comments, f)


def read_milestones():
    """Return a map milestone id -> json with the Lighthouse milestones"""
    fns = os.listdir(os.path.join(opt.srcdir, 'milestones'))
    rv = {}
    for fn in fns:
        logger.debug('importing milestone %s' % fn)
        data = json.load(open(
            os.path.join(opt.srcdir, 'milestones', fn)))
        rv[int(fn.split('-', 1)[0])] = data
    return rv

def convert_milestones(lhms):
    ghms = {}
    for i, (n, lhm) in zip(count(1), sorted(lhms.iteritems())):
        ghms[n] = convert_milestone(lhm, i)
    return ghms

def convert_milestone(lhm, number):
    lhm = lhm['milestone']
    ghm = {}
    ghm['number'] = number
    ghm['state'] = 'open' if lhm['open_tickets_count'] else 'closed'
    ghm['title'] = lhm['title']
    ghm['description'] = lhm['goals']
    ghm['created_at'] = lhm['created_at']
    if lhm['due_on']:
        ghm['due_on'] = lhm['due_on']
    return ghm

def save_milestones(ghms):
    dir = os.path.join(opt.destdir, 'milestones')
    if not os.path.exists(dir):
        os.makedirs(dir)
    for ghm in ghms.itervalues():
        save_milestone(dir, ghm)

def save_milestone(dir, ghm):
    logger.info('saving milestone %s - %s', ghm['number'], ghm['title'])
    fn = os.path.join(dir, '%d.json' % ghm['number'])
    with open(fn, 'w') as f:
        json.dump(ghm, f)


class ScriptError(Exception):
    """Controlled exception raised by the script."""

def parse_cmdline():
    from optparse import OptionParser
    parser = OptionParser(usage="%prog [options] SRCDIR DESTDIR",
        description="Convert issues exported from Lighthouse to GitHub")
    parser.add_option('--remap-until', type=int, metavar="N",
        help="change the ticket numbers from 1 to N to an higher number")
    parser.add_option('--remap-offset', type=int, metavar="M",
        help="add M to the tickets selected by --remap-until")
    parser.add_option('--map-user', metavar='OLD:NEW', action='append',
        help="map the lighthouse user name 'OLD' to the github username 'NEW'")
    parser.add_option('--fallback-user', metavar='NAME',
        help="set NAME as github username for unmapped users")

    opt, args = parser.parse_args()
    if len(args) <> 2:
        parser.error("two directories expected")
    if (opt.remap_until is None) != (opt.remap_offset is None):
        parser.error(
            "please specify both --remap-until and --remap-offset or none")

    opt.srcdir, opt.destdir = args

    opt.usermap = {}
    for s in opt.map_user:
        if ':' not in s:
            parser.error("bad user map: '%s'" % s)
        old, new = s.split(':', 1)
        opt.usermap[old] = new

    return opt

if __name__ == '__main__':
    try:
        sys.exit(main())

    except ScriptError, e:
        logger.error("%s", e)
        sys.exit(1)

    except Exception:
        logger.exception("unexpected error")
        sys.exit(1)

    except KeyboardInterrupt:
        logger.info("user interrupt")
        sys.exit(1)
