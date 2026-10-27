#!/usr/bin/env python3
"""Readable view of a syslog assessment final_assessment.json (or fleet_review.json).

Usage:
  python3 syslog_report_view.py <final_assessment.json>
  python3 syslog_report_view.py                 # newest /tmp/assessment_*/ final report
Shows the report on screen and saves it next to the JSON as <server>_results.txt
(fleet_results.txt for a fleet review).
Options:
  --top N       rows in each table (default 20)
  --level N     folder level used to group sources (1 = top folder, 2 = second, default 1)
  --quiet       save the file without printing the report on screen
  --read-minutes N     longest time Cribl should need to read one file (default 15) -> size limit
  --max-age-hours N    time limit so quiet logs still rotate (default 24 = daily)
  --cribl-cores N      physical cores available to Cribl, e.g. 8; capacity = N x 200 GB/day
  --gb-per-core-day N  inbound GB/day per physical core (default 200)
  --workgroup NAME     take cores from the worker-group table (see --list-workgroups)
  --site NAME          with --workgroup, only that site's row
  --cores-type vcpu    counts are vCPUs (halved to physical cores); default physical
  --available-pct N    share of the group free for this data (default 100)

Python 3.6+, standard library only. Read-only.
"""
import argparse
import collections
from datetime import datetime, timezone
import glob
import json
import os
import sys


LINES = []


def out(text=''):
    LINES.append(str(text))


def results_name(report):
    """<short server name>_results.txt, or fleet_results.txt for several servers."""
    hosts = sorted({str(s.get('hostname') or '') for s in report.get('servers') or [] if s.get('hostname')})
    if report.get('sample_label') == 'fleet_review' or len(hosts) != 1:
        return 'fleet_results.txt'
    short = hosts[0].split('.')[0]
    safe = ''.join(c if c.isalnum() or c in '-_' else '_' for c in short) or 'server'
    return safe + '_results.txt'


def save_results(text, json_path, name):
    """Write next to the JSON; fall back to the current directory if that is not writable."""
    for directory in (os.path.dirname(os.path.abspath(json_path)), os.getcwd()):
        target = os.path.join(directory, name)
        try:
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, 'O_NOFOLLOW', 0), 0o600)
            with os.fdopen(fd, 'w', encoding='utf-8', errors='backslashreplace') as stream:
                stream.write(text)
            return target
        except OSError:
            continue
    return None


def show(text):
    encoding = getattr(sys.stdout, 'encoding', None) or 'ascii'
    sys.stdout.write(text.encode(encoding, 'backslashreplace').decode(encoding))
    sys.stdout.flush()


def n(value, digits=3):
    if value is None:
        return '-'
    try:
        return '%.*f' % (digits, float(value))
    except (TypeError, ValueError):
        return str(value)


def gb(value):
    return n((value or 0) / 1e9, 2)


def section(title):
    out()
    out('=' * 78)
    out(title)
    out('=' * 78)


def table(headers, rows, widths):
    fmt = '  '.join('%%%s%ds' % ('-' if i == len(headers) - 1 else '', w) for i, w in enumerate(widths))
    out(fmt % tuple(headers))
    out(fmt % tuple('-' * w for w in widths))
    for row in rows:
        out(fmt % tuple(str(c) for c in row))


def group_key(path, level):
    parts = str(path).split('/')
    return '/'.join(parts[:level]) if len(parts) > level else '(top level)'


def fleet(report):
    f = report.get('fleet_vs_cribl') or {}
    section('RESULT: COMBINED WRITE RATE vs CRIBL INGESTION (%s MB/sec)' % n(f.get('cribl_rate_MB_per_second_shared'), 2))
    out('Result:   %s' % f.get('status', '-'))
    out('          %s' % f.get('verdict', ''))
    if f.get('combined_peak_minute_MB_per_second') is not None:
        out('Peak minute:      %s MB/sec at %s (%s%% of the Cribl rate)' % (
            n(f['combined_peak_minute_MB_per_second']), f.get('combined_peak_minute_utc'), n(f.get('combined_peak_percent_of_rate'), 0)))
        out('Average:          %s MB/sec   (95th percentile minute %s MB/sec)' % (
            n(f.get('combined_average_MB_per_second')), n(f.get('combined_p95_minute_MB_per_second'))))
        out('Minutes over:     %s of %s (%s%%); longest stretch %s min' % (
            f.get('minutes_above_rate'), f.get('minutes_combined'), n(f.get('percent_minutes_above_rate'), 1),
            f.get('longest_stretch_above_rate_minutes')))
        rate = f.get('cribl_rate_MB_per_second_shared') or 0
        avg = f.get('combined_average_MB_per_second') or 0
        if rate:
            out('Average is %sx the Cribl rate; about %s TB/day written.' % (n(avg / rate, 1), n(avg * 86400 / 1e6, 2)))
    for key, label in (('coverage_warning', 'Coverage'), ('servers_without_measurements', 'No measurements')):
        if f.get(key):
            out('%s: %s' % (label, f[key]))
    run = report.get('assessment_run') or {}
    if run:
        out('Run: %s h planned, scan every %s s, %s - %s' % (
            n(run.get('runtime_hours'), 2), n(run.get('scan_interval_seconds'), 0),
            'completed as planned' if run.get('completed_as_planned') else 'INCOMPLETE', run.get('note')))


def server_view(s, top, level):
    static = s.get('final_static_evidence') or {}
    inv = static.get('final_inventory') or {}
    section('SERVER %s   directory %s' % (s.get('hostname'), s.get('directory')))
    out('Measured:      %s hours' % n((s.get('sampled_seconds') or 0) / 3600, 2))
    out('Write rate:    average %s MB/sec, peak interval %s MB/sec  (~%s TB/day)' % (
        n(s.get('weighted_sample_average_MB_per_second')), n(s.get('peak_sample_MB_per_second')),
        n((s.get('weighted_sample_average_MB_per_second') or 0) * 86400 / 1e6, 2)))
    if inv:
        out('Files:         %s files, %s GB total, largest %s GB, filesystem free %s GB' % (
            inv.get('unique_regular_files'), gb(inv.get('logical_bytes')), gb(inv.get('largest_file_bytes')), gb(inv.get('filesystem_free_bytes'))))
        ages = inv.get('files_by_modification_age') or {}
        if ages:
            out('File ages:     ' + ', '.join('%s %s' % (k, v) for k, v in ages.items()))
    sysinfo = static.get('system_info') or {}
    cpus = sysinfo.get('logical_cpus_visible_to_os')
    if cpus or sysinfo.get('os_visible_memory_bytes'):
        out('CPU / memory:  %s logical CPUs (%s usable by this process), %s GB memory, %s' % (
            cpus or '-', sysinfo.get('cpu_affinity_count') or '-', gb(sysinfo.get('os_visible_memory_bytes')),
            ((sysinfo.get('os_release') or {}).get('PRETTY_NAME') or sysinfo.get('os') or '')))
    rate = (s.get('rotation_proposal') or {}).get('cribl_rate_MB_per_second_shared') or 2.4
    avg_mb = s.get('weighted_sample_average_MB_per_second') or 0
    peak_mb = s.get('peak_sample_MB_per_second') or 0
    if avg_mb:
        out('Capacity estimate IF %s MB/sec is per worker process: average needs ~%s processes, peak ~%s%s.' % (
            n(rate, 2), n(avg_mb / rate, 1), n(peak_mb / rate, 1),
            ('; this server has %s logical CPUs (shared with syslog)' % cpus) if cpus else ''))
        out('  Cribl rule of thumb (Intel/AMD): 1 physical core per 400 GB/day IN+OUT. This server writes ~%s GB/day.'
            % n(avg_mb * 86400 / 1000, 0))
    paths = s.get('per_path') or []
    growing = [p for p in paths if (p.get('peak_sample_MB_per_second') or 0) > 0]
    out('Growing files: %d of %d measured (%d idle)' % (len(growing), len(paths), len(paths) - len(growing)))

    # Where the volume comes from
    rate, count, active = collections.Counter(), collections.Counter(), collections.Counter()
    for p in paths:
        key = group_key(p['path'], level)
        rate[key] += p.get('average_MB_per_second_when_observed') or 0
        count[key] += 1
        active[key] += (p.get('peak_sample_MB_per_second') or 0) > 0
    size = collections.Counter()
    for f in inv.get('all_files') or []:
        size[group_key(f['path'], level)] += f.get('bytes') or 0
    total = sum(rate.values()) or 1
    out()
    out('WRITE RATE BY SOURCE FOLDER (level %d)' % level)
    table(['MB/sec', '% of total', 'files', 'growing', 'GB on disk', 'folder'],
          [(n(v), n(v / total * 100, 1), count[k], active[k], gb(size[k]), k) for k, v in rate.most_common(top)],
          [9, 10, 7, 7, 10, 30])

    out()
    out('BUSIEST FILES (by peak write rate)')
    table(['avg MB/s', 'peak MB/s', 'GB/day', 'file'],
          [(n(p.get('average_MB_per_second_when_observed')), n(p.get('peak_sample_MB_per_second')),
            n((p.get('average_MB_per_second_when_observed') or 0) * 86400 / 1000, 1), p['path'])
           for p in sorted(growing, key=lambda p: -(p.get('peak_sample_MB_per_second') or 0))[:top]],
          [9, 9, 7, 40])

    if inv.get('largest_files'):
        out()
        out('LARGEST FILES AT SETUP')
        table(['GB', 'file'], [(gb(f.get('bytes')), f.get('path')) for f in inv['largest_files']], [9, 40])

    rp = s.get('rotation_proposal') or {}
    out()
    out('ROTATION CANDIDATES: %s   confidence %s   (file age objective %s h, size ceiling %s GB)' % (
        rp.get('status'), rp.get('confidence', '-'), (rp.get('objectives') or {}).get('target_file_age_hours'),
        (rp.get('objectives') or {}).get('candidate_size_gb')))
    for gap in rp.get('blocking_gaps') or []:
        out('  Blocking: ' + gap)
    for gap in rp.get('advisory_gaps') or []:
        out('  Advisory: ' + gap)
    cands = sorted(rp.get('per_path') or [], key=lambda p: -(p.get('conditional_size_GB') or 0))
    if cands:
        table(['size GB', 'or hours', 'peak MB/s', 'file'],
              [(n(p.get('conditional_size_GB'), 2), p.get('conditional_time_hours'), n(p.get('sampled_peak_MB_per_second')), p['path']) for p in cands[:top]],
              [9, 8, 9, 40])
        sizes = [p['conditional_size_GB'] for p in cands if p.get('conditional_size_GB') is not None]
        if sizes:
            out('  Across %d files: largest %s GB, median %s GB at the %s-hour age objective.' % (
                len(sizes), n(max(sizes), 2), n(sorted(sizes)[len(sizes) // 2], 3), cands[0].get('conditional_time_hours')))

    pol = static.get('policies') or {}
    lr = pol.get('logrotate') or {}
    out()
    out('EXISTING LOGROTATE RULES (from setup snapshot)')
    glob_dirs = ', '.join('%s %s' % (d.get('directive'), d.get('value') or '') for d in lr.get('global_directives_in_encounter_order') or [])
    out('  Global: ' + (glob_dirs or 'none found'))
    for st in lr.get('stanzas') or []:
        out('  %s:%s  %s' % (st.get('file'), st.get('line'), ' '.join(st.get('targets') or [])))
        out('      ' + (', '.join('%s %s' % (d.get('directive'), d.get('value') or '') for d in st.get('local_directives_in_order') or []) or 'no directives'))
    for item in pol.get('syslog_rotation_settings') or []:
        out('  %s %s:%s  %s %s' % (item.get('product'), item.get('file'), item.get('line'), item.get('setting'), item.get('configured_value') or item.get('note') or ''))

    cron = static.get('cron_policies') or {}
    timer = ((cron.get('logrotate_timer_status') or {}).get('units') or {}).get('logrotate.timer') or {}
    props = timer.get('properties') or {}
    out()
    out('HOW OFTEN LOGROTATE RUNS')
    out('  logrotate.timer: %s %s; schedule %s; last run %s' % (
        props.get('ActiveState', timer.get('status', '-')), props.get('UnitFileState', ''), props.get('TimersCalendar', '-'), props.get('LastTriggerUSec', '-')))
    for e in cron.get('cron_entries') or []:
        if e.get('rotation_candidate_by_command') or 'logrotate' in str(e.get('file')):
            out('  cron %s:%s  %s  (%s)' % (e.get('file'), e.get('line'), e.get('schedule_as_written'), e.get('schedule_description')))
    for f in cron.get('files') or []:
        if f.get('rotation_candidate_by_name'):
            out('  periodic job: %s' % f.get('path'))

    soft = ((static.get('system_info') or {}).get('software')) or {}
    if soft:
        out()
        out('VERSIONS: ' + '; '.join('%s %s' % (k, (v or {}).get('version') or (v or {}).get('status')) for k, v in soft.items()))
    if s.get('hourly_observations'):
        out()
        out('HOURLY AVERAGE MB/sec')
        for block in s['hourly_observations']:
            out('  from %s: %s' % (block.get('started_utc'), ', '.join(n(h.get('average_MB_per_second')) for h in block.get('elapsed_hours') or [])))
    if s.get('evidence_gaps'):
        out()
        out('EVIDENCE GAPS')
        for gap in s['evidence_gaps']:
            out('  - ' + gap)


NICE_SIZES_GB = (0.1, 0.25, 0.5, 1, 2, 3, 4, 5, 8, 10, 15, 20, 25, 50, 100)
CHECK_MINUTES = (1, 2, 5, 10, 15, 30, 60)


def nice_size(raw_gb):
    """Largest practical size at or below raw_gb."""
    fitting = [v for v in NICE_SIZES_GB if v <= raw_gb]
    return fitting[-1] if fitting else round(raw_gb, 2)


def human_minutes(minutes):
    if minutes is None:
        return '-'
    if minutes >= 120:
        return '%s h' % n(minutes / 60, 1)
    return '%s min' % n(minutes, 0)


def minute_profile(series):
    """Quiet (5th percentile), average and peak MB/sec from [[minute, bytes, seconds], ...]."""
    rows = [(m, b / sec / 1e6) for m, b, sec in series or [] if sec >= 30]
    if not rows:
        return None
    values = sorted(v for _, v in rows)
    pick = lambda q: values[min(len(values) - 1, int(round(q * (len(values) - 1))))]
    peak_minute, peak = max(rows, key=lambda r: r[1])
    quiet_minute, quiet = min(rows, key=lambda r: r[1])
    return {'minutes': len(rows), 'quiet_p05': pick(0.05), 'lowest': quiet, 'lowest_at': quiet_minute,
            'average': sum(values) / len(values), 'p95': pick(0.95), 'peak': peak, 'peak_at': peak_minute}


def utc(minute):
    return datetime.fromtimestamp(minute, timezone.utc).strftime('%m-%d %H:%M UTC')


def combined_series(report):
    """Per-minute combined MB/sec over minutes measured on every server, plus per-server series."""
    per_server, minutes = [], {}
    for srv in report.get('servers') or []:
        rows = {m: b / sec / 1e6 for m, b, sec in srv.get('minute_series') or [] if sec >= 30}
        if rows:
            per_server.append((str(srv.get('hostname') or '').split('.')[0], rows))
    if not per_server:
        return per_server, []
    common = set.intersection(*(set(rows) for _, rows in per_server))
    return per_server, sorted((m, sum(rows[m] for _, rows in per_server)) for m in common)


def stretch(minutes):
    longest, start, run, run_start, prev = 0, None, 0, None, None
    for m in minutes:
        run, run_start = (run + 1, run_start) if prev is not None and m - prev == 60 else (1, m)
        if run > longest:
            longest, start = run, run_start
        prev = m
    return longest, start


# Worker groups and core counts from the customer inventory spreadsheet (updated 2026-10-27, 85 servers / 1,564 cores).
# (site, worker group, worker nodes today, cores today). Update with --workgroups-file.
WORKGROUPS = [
    ('GCP (new HEC group)', 'New GCP HEC worker group', 0, 0),
    ('Middletown', 'prod-aetna-mid-streaming-hec', 2, 32),
    ('Windsor', 'prod-aetna-windmz-streaming-s2s', 3, 48),
    ('Windsor', 'prod-aetna-win-streaming-hec', 2, 32),
    ('RI CVS 1 Dr', 'prod-cvs-woon-streaming-s2s', 16, 376),
    ('RI CVS 1 Dr', 'prod-cvs-azeast2-streaming-s2s', 1, 24),
    ('RI CVS 1 Dr', 'prod-cvs-woon-collection-rest', 2, 48),
    ('AZ Scottsdale', '(none in inventory)', 1, 24),
    ('AZ Scottsdale', 'prod-cvs-shea-streaming-s2s', 11, 264),
    ('Middletown', '(none in inventory)', 1, 6),
    ('Middletown', 'prod-aetna-mid-streaming-s2s', 5, 80),
    ('Middletown', 'prod-aetna-middmz-streaming-s2s', 6, 80),
    ('Middletown', 'prod-aetna-midpci-streaming-s2s', 1, 16),
    ('Middletown', 'prod-aetna-win-streaming-s2s', 1, 16),
    ('Middletown', 'prod-aetna-winpci-streaming-s2s', 1, 6),
    ('Middletown', 'prod-cvs-azeast2-streaming-s2s', 1, 8),
    ('Windsor', '(none in inventory)', 2, 32),
    ('Windsor', 'prod-aetna-win-streaming-s2s', 7, 64),
    ('East US 2 Azure', 'prod-aetna-windmz-streaming-s2s', 1, 16),
    ('East US 2 Azure', 'prod-cvs-azeast2-streaming-s2s', 5, 144),
    ('Central US Azure', 'prod-aetna-azcentral-streaming-s2s', 2, 32),
    ('Central US Azure', 'prod-cvs-azcentral-streaming-s2s', 3, 64),
    ('East US 4 GCP', 'prod-cvs-gceast-streaming-s2s', 3, 48),
    ('East US 4 GCP', 'prod-gcpuseast4-logs-splunk', 1, 16),
    ('Las Vegas', 'prod-aetna-veg-streaming-s2s', 6, 88),
    ('Las Vegas', '(none in inventory)', 1, 0),
]


def load_workgroups(path):
    """CSV from the inventory spreadsheet. Columns are found by header name:
    a 'worker group' column, a 'servers' column and a 'cores' column; 'site' is optional.
    Extra columns (e.g. Chart label) and the Total row are ignored."""
    import csv
    with open(path, newline='', encoding='utf-8-sig', errors='replace') as stream:
        table = [row for row in csv.reader(stream) if any(cell.strip() for cell in row)]
    header_index = None
    for i, row in enumerate(table[:10]):
        lowered = [c.strip().lower() for c in row]
        if any('worker group' in c for c in lowered) and any('core' in c for c in lowered):
            header_index = i
            break
    if header_index is None:
        raise SystemExit('Could not find a header row with "worker group" and "cores" columns in ' + path)
    header = [c.strip().lower() for c in table[header_index]]
    def column(*words):
        for j, name in enumerate(header):
            if all(w in name for w in words):
                return j
        return None
    group_col, cores_col = column('worker group'), column('core')
    servers_col, site_col = column('server'), column('site')
    rows = []
    for row in table[header_index + 1:]:
        cell = lambda j: row[j].strip() if j is not None and j < len(row) else ''
        group, site = cell(group_col), cell(site_col)
        if not group or (site or group).lower().startswith('total'):
            continue
        try:
            cores = float(cell(cores_col).replace(',', '') or 0)
            servers = int(float(cell(servers_col).replace(',', '') or 0))
        except ValueError:
            continue
        rows.append((site or '-', group, servers, cores))
    if not rows:
        raise SystemExit('No worker-group rows found in ' + path)
    return rows


def list_workgroups(rows):
    show('%-18s  %-36s  %7s  %6s\n' % ('site', 'worker group', 'servers', 'cores'))
    for site, group, servers, cores in rows:
        show('%-18s  %-36s  %7s  %6s\n' % (site, group, servers, n(cores, 0)))
    show('%-18s  %-36s  %7s  %6s\n' % ('TOTAL', '', sum(r[2] for r in rows), n(sum(r[3] for r in rows), 0)))


def pick_workgroup(rows, name, site=None):
    """Exact name, else a unique partial match; sums the group across sites unless --site is given."""
    key = name.lower()
    usable = [r for r in rows if not r[1].startswith('(none')]
    exact = [r for r in usable if r[1].lower() == key]
    matches = exact or [r for r in usable if key in r[1].lower()]
    groups = sorted({r[1] for r in matches})
    if not matches:
        raise SystemExit('No worker group matches "%s". Use --list-workgroups.' % name)
    if len(groups) > 1:
        raise SystemExit('"%s" matches several worker groups: %s. Use the full name.' % (name, ', '.join(groups)))
    if site:
        matches = [r for r in matches if r[0].lower() == site.lower()]
        if not matches:
            raise SystemExit('Worker group %s has no row for site "%s".' % (groups[0], site))
    return groups[0], matches


def capacity_check(report, cores, gb_per_core_day, headroom_pct, source=None):
    """Compare measured per-minute rates with N physical cores x guideline throughput."""
    per_core = gb_per_core_day * 1000 / 86400          # MB/sec per physical core
    section('CRIBL CAPACITY CHECK (guideline %s GB/day per physical core = %s MB/sec)' % (n(gb_per_core_day, 0), n(per_core, 2)))
    per_server, combined = combined_series(report)
    if not per_server:
        out('No per-minute measurements available.')
        return
    rows = []
    for host, series in per_server:
        values = sorted(series.values())
        avg = sum(values) / len(values)
        rows.append((host, values, avg))
    if len(per_server) > 1 and combined:
        values = sorted(v for _, v in combined)
        rows.append(('ALL SERVERS', values, sum(values) / len(values)))
    pick = lambda vals, q: vals[min(len(vals) - 1, int(round(q * (len(vals) - 1))))]
    out('Physical cores needed at the guideline (add %s%% headroom for OS, bursts and growth):' % n(headroom_pct, 0))
    table(['average', 'busy 95%', 'peak', 'GB/day', 'cores avg', 'cores peak', 'with headroom', 'server'],
          [(n(a), n(pick(v, 0.95)), n(v[-1]), n(a * 86400 / 1000, 0), n(a / per_core, 1), n(v[-1] / per_core, 1),
            n(v[-1] / per_core * (1 + headroom_pct / 100.0), 1), h) for h, v, a in rows],
          [8, 8, 8, 8, 9, 10, 13, 20])
    if not cores:
        out()
        out('No Cribl worker group chosen. Add --workgroup NAME (see --list-workgroups) or --cribl-cores N to check capacity.')
        return
    capacity = cores * per_core
    out()
    for line in source or []:
        out(line)
    out('With %s physical cores: capacity %s MB/sec (%s GB/day)' % (n(cores, 1).rstrip('0').rstrip('.'), n(capacity, 2), n(capacity * 86400 / 1000, 0)))
    series_list = [(h, sorted(rows_.items())) for h, rows_ in per_server]
    if len(per_server) > 1 and combined:
        series_list.append(('ALL SERVERS', combined))
    verdict_rows = []
    for host, series in series_list:
        over = [m for m, v in series if v > capacity]
        longest, start = stretch(over)
        values = [v for _, v in series]
        avg, peak = sum(values) / len(values), max(values)
        backlog = sum(max(0.0, v - capacity) * 60 for v in values) / 1000
        verdict_rows.append((host, '%s%%' % n(avg / capacity * 100, 0), '%s%%' % n(peak / capacity * 100, 0),
                             '%d of %d' % (len(over), len(values)), '%d min' % longest, n(backlog, 2),
                             'OK' if not over else ('QUEUES AT PEAKS' if avg <= capacity else 'QUEUES - TOO SMALL')))
    table(['avg used', 'peak used', 'minutes over', 'longest', 'GB behind', 'result', 'server'],
          [(r[1], r[2], r[3], r[4], r[5], r[6], r[0]) for r in verdict_rows], [8, 9, 13, 8, 9, 18, 20])
    out('  "GB behind" = data that would queue during the measured minutes above capacity (it drains when the rate drops).')
    out('  If these cores are on the syslog server itself, syslog-ng shares them; count only the cores Cribl can use.')
    out('  The guideline assumes moderate processing; heavy parsing needs more cores, pass-through fewer. Validate in a pilot.')


def blanket_recommendation(report, read_minutes, max_age_hours, min_rotation_minutes, top, cores=None):
    """Data-driven blanket rule: rotate at SIZE OR after AGE, whichever comes first."""
    fleet_info = report.get('fleet_vs_cribl') or {}
    rate = fleet_info.get('cribl_rate_MB_per_second_shared') or report.get('cribl_rate_MB_per_second_shared') or 2.4
    files, profiles, combined = [], [], collections.defaultdict(lambda: [0.0, 0.0, 0])
    servers = report.get('servers') or []
    for s in servers:
        host = str(s.get('hostname') or '').split('.')[0]
        prof = minute_profile(s.get('minute_series'))
        profiles.append((host, prof))
        for m, b, sec in s.get('minute_series') or []:
            if sec >= 30:
                combined[m][0] += b / sec / 1e6
                combined[m][2] += 1
        # Per-file quiet-time rates are not recorded; estimate them from the server's quiet/average ratio.
        ratio = prof['quiet_p05'] / prof['average'] if prof and prof['average'] else None
        for p in s.get('per_path') or []:
            peak = p.get('peak_sample_MB_per_second') or 0
            if peak > 0:
                avg = p.get('average_MB_per_second_when_observed') or 0
                files.append({'name': host + ':' + p['path'], 'peak': peak, 'avg': avg,
                              'quiet': avg * ratio if ratio is not None else None})
    section('BLANKET ROTATION RECOMMENDATION (derived from measured rates)')
    if any(prof for _, prof in profiles):
        out('Traffic profile (per-minute write rate, MB/sec):')
        rows = [(host, n(prof['quiet_p05']), n(prof['average']), n(prof['p95']), n(prof['peak']),
                 utc(prof['peak_at']), utc(prof['lowest_at']), prof['minutes']) for host, prof in profiles if prof]
        full = [(m, v[0]) for m, v in combined.items() if v[2] == len([1 for _, prof in profiles if prof])]
        if len(rows) > 1 and full:
            series = [[m, v * 60e6, 60.0] for m, v in full]
            c = minute_profile(series)
            rows.append(('ALL SERVERS', n(c['quiet_p05']), n(c['average']), n(c['p95']), n(c['peak']), utc(c['peak_at']), utc(c['lowest_at']), c['minutes']))
        table(['quiet*', 'average', 'busy**', 'peak', 'peak at', 'quietest at', 'minutes', 'server'],
              [(r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[0]) for r in rows], [8, 8, 8, 8, 16, 16, 7, 20])
        out('  * quiet = 5th percentile minute   ** busy = 95th percentile minute')
        short = [host for host, prof in profiles if prof and prof['minutes'] < 360]
        if short:
            out('  Measured less than 6 hours on: %s. Quiet and busy periods may not both be included.' % ', '.join(short))
        out()
    if not files:
        out('No growing files were measured; no recommendation.')
        return
    raw_size_gb = rate * read_minutes * 60 / 1000
    size_gb = nice_size(raw_size_gb)
    size_mb = size_gb * 1000
    age_minutes = max_age_hours * 60
    # Which limit each file reaches first, at its peak and at its average rate.
    for f in files:
        f['minutes_to_size_peak'] = size_mb / f['peak'] / 60
        f['minutes_to_size_avg'] = size_mb / f['avg'] / 60 if f['avg'] > 0 else None
        f['minutes_to_size_quiet'] = size_mb / f['quiet'] / 60 if f['quiet'] else None
        f['size_at_age_gb'] = f['avg'] * age_minutes * 60 / 1000
    by_size = [f for f in files if f['minutes_to_size_peak'] < age_minutes]
    by_time = [f for f in files if f['minutes_to_size_peak'] >= age_minutes]
    out('Recommended blanket rule:  rotate at %s GB OR after %s hours, whichever comes first.'
        % (n(size_gb, 2).rstrip('0').rstrip('.'), n(max_age_hours, 1).rstrip('0').rstrip('.')))
    out()
    out('Why %s GB: the largest file Cribl can read in about %s minutes at %s MB/sec'
        % (n(size_gb, 2).rstrip('0').rstrip('.'), n(read_minutes, 0), n(rate, 2)))
    out('           (%s MB/sec x %s s = %s GB, rounded down to a practical size).'
        % (n(rate, 2), n(read_minutes * 60, 0), n(raw_size_gb, 2)))
    out('Why %s hours: the maximum age for quiet logs, so low-volume files rotate on time instead of'
        % n(max_age_hours, 1).rstrip('0').rstrip('.'))
    out('           producing many tiny files. Change with --max-age-hours; change the read time with --read-minutes.')
    out()
    out('Effect on the %d growing files measured:' % len(files))
    out('  %d rotate on SIZE (reach %s GB before %s hours at their peak rate)'
        % (len(by_size), n(size_gb, 2).rstrip('0').rstrip('.'), n(max_age_hours, 1).rstrip('0').rstrip('.')))
    out('  %d rotate on TIME (every %s hours; typical file at rotation %s GB, largest %s GB)' % (
        len(by_time), n(max_age_hours, 1).rstrip('0').rstrip('.'),
        n(sorted(f['size_at_age_gb'] for f in by_time)[len(by_time) // 2], 3) if by_time else '-',
        n(max(f['size_at_age_gb'] for f in by_time), 3) if by_time else '-'))
    if by_size:
        out()
        out('Files that rotate on size (busiest first):')
        when = lambda m: ('every ' + human_minutes(m)) if m and m < age_minutes else 'on time'
        table(['peak MB/s', 'avg MB/s', 'at peak', 'at average', 'at quiet*', 'file'],
              [(n(f['peak']), n(f['avg']), when(f['minutes_to_size_peak']), when(f['minutes_to_size_avg']),
                when(f['minutes_to_size_quiet']), f['name']) for f in sorted(by_size, key=lambda f: -f['peak'])[:top]],
              [9, 9, 14, 14, 14, 40])
        out('  * quiet-time rotation is estimated from the server\'s quiet/average ratio (per-file quiet rates are not recorded).')
        steady = [f for f in by_size if f['minutes_to_size_avg'] and f['minutes_to_size_avg'] < age_minutes]
        out('  %d of these rotate on size even at their average rate; %d only at busy times and rotate on time otherwise.'
            % (len(steady), len(by_size) - len(steady)))
    busiest = max(files, key=lambda f: f['peak'])
    fastest = busiest['minutes_to_size_peak']
    if fastest < min_rotation_minutes:
        out()
        out('WARNING: the busiest file (%s) would reach %s GB every %s at its peak.' % (
            busiest['name'], n(size_gb, 2).rstrip('0').rstrip('.'), human_minutes(fastest)))
        out('         That is a very high rotation rate; it points to a capacity problem rather than a rotation setting.')
    # How often rotation must be checked so files overshoot the size limit by at most 25%.
    allowed = 0.25 * size_mb / busiest['peak'] / 60
    fitting = [m for m in CHECK_MINUTES if m <= allowed]
    check = fitting[-1] if fitting else CHECK_MINUTES[0]
    out()
    out('Rotation check frequency: at least every %s minutes, so the busiest file overshoots %s GB by no more than'
        % (check, n(size_gb, 2).rstrip('0').rstrip('.')))
    out('  about 25%% (%s MB/sec x %s min = %s GB extra). A once-a-day logrotate run (RHEL 8 default) cannot enforce a size limit;'
        % (n(busiest['peak']), check, n(busiest['peak'] * check * 60 / 1000, 2)))
    out('  use a frequent logrotate check or size/time rotation inside rsyslog/syslog-ng.')
    avg_total = fleet_info.get('combined_average_MB_per_second')
    peak_total = fleet_info.get('combined_peak_minute_MB_per_second')
    out()
    if cores:
        out('CAPACITY: see CRIBL CAPACITY CHECK above for %s physical cores. The size limit still uses %s MB/sec as the' % (n(cores, 0), n(rate, 2)))
        out('          speed at which one file is read (one worker process reads one file).')
    elif avg_total is not None and avg_total > rate:
        out('CAPACITY: combined average %s MB/sec is %sx the %s MB/sec Cribl rate. No rotation setting prevents queuing;'
            % (n(avg_total), n(avg_total / rate, 1), n(rate, 2)))
        out('          the backlog grows by about %s GB per hour. Ingestion capacity must increase first.'
            % n((avg_total - rate) * 3600 / 1000, 1))
    elif peak_total is not None and peak_total > rate:
        out('CAPACITY: average fits within %s MB/sec but the peak minute (%s MB/sec) exceeds it; expect queuing at peaks.'
            % (n(rate, 2), n(peak_total)))
    elif peak_total is not None:
        out('CAPACITY: combined peak %s MB/sec is within the %s MB/sec Cribl rate (%s%% used).'
            % (n(peak_total), n(rate, 2), n(peak_total / rate * 100, 0)))
    if len(report.get('servers') or []) == 1 and report.get('sample_label') != 'fleet_review':
        out('Note: this covers one server. Run the fleet review on all servers for the fleet-wide rule and combined rate.')
    out('Based on the measured period only; a busier period moves files from TIME to SIZE. Validate in a pilot.')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('report', nargs='?')
    parser.add_argument('--top', type=int, default=20)
    parser.add_argument('--level', type=int, default=1)
    parser.add_argument('--quiet', action='store_true', help='save the results file without printing it')
    parser.add_argument('--read-minutes', type=float, default=15, help='longest time Cribl should need to read one file at the Cribl rate (default 15)')
    parser.add_argument('--max-age-hours', type=float, default=24, help='time limit so quiet logs still rotate (default 24 = daily)')
    parser.add_argument('--min-rotation-minutes', type=float, default=10, help='warn if the busiest file would rotate more often than this (default 10)')
    parser.add_argument('--cribl-cores', type=float, help='physical cores available to Cribl; checks every measured minute against that capacity')
    parser.add_argument('--gb-per-core-day', type=float, default=200, help='inbound GB/day one physical core handles (default 200 = 400 GB/day IN+OUT guideline)')
    parser.add_argument('--headroom-pct', type=float, default=20, help='headroom added to the cores-needed estimate (default 20)')
    parser.add_argument('--workgroup', help='Cribl worker group name (or unique part of it); its cores are taken from the inventory table')
    parser.add_argument('--site', help='with --workgroup: use only this site row, for groups listed at several sites')
    parser.add_argument('--cores-type', choices=['physical', 'vcpu'], default='physical', help='whether inventory/--cribl-cores counts are physical cores or vCPUs (vCPU = half a physical core)')
    parser.add_argument('--available-pct', type=float, default=100, help='share of the group cores free for this syslog data, e.g. 50 if half is used by other traffic (default 100)')
    parser.add_argument('--reserve-cores-per-node', type=float, default=0, help='cores per worker node not running worker processes (e.g. 2 for the Cribl default of CPUs minus 2)')
    parser.add_argument('--workgroups-file', help='CSV of site,worker group,servers,cores to use instead of the built-in table')
    parser.add_argument('--list-workgroups', action='store_true', help='print the worker-group table and exit')
    args = parser.parse_args()
    groups = load_workgroups(args.workgroups_file) if args.workgroups_file else WORKGROUPS
    if args.list_workgroups:
        list_workgroups(groups)
        return 0
    source = []
    cores = args.cribl_cores
    nodes = None
    if args.workgroup:
        name, rows = pick_workgroup(groups, args.workgroup, args.site)
        cores = sum(r[3] for r in rows)
        if not cores:
            raise SystemExit('Worker group %s has 0 cores in the inventory (planned or not built yet). '
                             'Use --cribl-cores N with the planned size instead.' % name)
        nodes = sum(r[2] for r in rows)
        source.append('Worker group: %s  (%s)' % (name, '; '.join('%s: %s node%s, %s cores' % (r[0], r[2], '' if r[2] == 1 else 's', n(r[3], 0)) for r in rows)))
        if len(rows) > 1:
            source.append('  Listed at %d sites and added together; use --site to pick one.' % len(rows))
    if cores:
        listed = cores
        if nodes and args.reserve_cores_per_node:
            cores -= nodes * args.reserve_cores_per_node
        if args.cores_type == 'vcpu':
            cores /= 2.0
        cores *= args.available_pct / 100.0
        source.append('Cores: %s listed%s%s%s -> %s physical cores used for this check.' % (
            n(listed, 0),
            ', minus %s reserved on %s nodes' % (n(args.reserve_cores_per_node, 0), nodes) if nodes and args.reserve_cores_per_node else '',
            ', vCPUs halved to physical' if args.cores_type == 'vcpu' else ' (treated as physical cores)',
            ', %s%% available for this data' % n(args.available_pct, 0) if args.available_pct != 100 else '',
            n(cores, 1).rstrip('0').rstrip('.')))
        source.append('  These groups already carry today\'s traffic; use --available-pct for the share free for this data.')
    path = args.report
    if not path:
        found = sorted(glob.glob('/tmp/assessment_*/syslog_*final_assessment.json'), key=os.path.getmtime)
        if not found:
            parser.error('give the path to a final_assessment.json')
        path = found[-1]
    with open(path, 'rb') as stream:
        report = json.loads(stream.read().decode('utf-8'))
    out('SYSLOG ASSESSMENT REPORT VIEW')
    out('Source: %s' % path)
    out('Generated: %s' % datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC'))
    fleet(report)
    capacity_check(report, cores, args.gb_per_core_day, args.headroom_pct, source)
    blanket_recommendation(report, args.read_minutes, args.max_age_hours, args.min_rotation_minutes, args.top, cores)
    for s in report.get('servers') or []:
        server_view(s, args.top, max(1, args.level))
    common = report.get('common_policy') or {}
    section('COMMON ROTATION CANDIDATE')
    out('%s  %s' % (common.get('status'), common.get('reason', '')))
    if common.get('candidate_size_GB'):
        out('rotate at ~%s GB OR after %s hours (manual review; smallest per-file candidate)' % (n(common['candidate_size_GB'], 2), common.get('candidate_time_hours')))
    if report.get('missing_servers'):
        out('Missing expected servers: ' + ', '.join(report['missing_servers']))
    if report.get('errors'):
        out('Input errors: %d' % len(report['errors']))
    text = '\n'.join(LINES) + '\n'
    saved = save_results(text, path, results_name(report))
    if not args.quiet:
        show(text)
    show(('\nSaved: %s\n' % saved) if saved else '\nCould not save the results file (directory not writable).\n')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except BrokenPipeError:
        pass
