"""
spam_review.py
Finds accounts that look like bot signups, for the admin to review and
delete. Only accounts that never did anything (no analyses, no Point
Play, no profile, no team, never used TennisAC as a guest first) are
suspects at all; the signals below say how strongly each one looks like a
bot. Nothing is deleted here.
"""
import re
from datetime import timedelta

# Throwaway inbox services bots sign up with.
DISPOSABLE_DOMAINS = {
    'mailinator.com', 'guerrillamail.com', 'guerrillamail.net', 'sharklasers.com', '10minutemail.com',
    'temp-mail.org', 'tempmail.com', 'tempmail.net', 'yopmail.com', 'trashmail.com', 'getnada.com',
    'dispostable.com', 'maildrop.cc', 'mailnesia.com', 'throwawaymail.com', 'fakeinbox.com',
    'emailondeck.com', 'mohmal.com', 'tempail.com', 'burnermail.io',
}

# A burst: this many inactive accounts made within BURST_WINDOW of each other.
BURST_SIZE = 3
BURST_WINDOW = timedelta(minutes=10)

SIGNAL_LABELS = {
    'link_in_name': 'Link in the name',
    'random_name': 'Random-looking name',
    'throwaway_email': 'Throwaway email service',
    'burst': 'Made in a burst of empty accounts',
}


def _looks_random(name):
    """Names like 'xKqzPwtrB' or 'gHt7712kL': one long token with digits,
    random capitals, or a long run of consonants."""
    token = name.strip()
    if ' ' in token or len(token) < 8:
        return False
    # Digits in the middle ("gHt7712kL"), not a person's "alex2008".
    if re.search(r'\d[A-Za-z]', token):
        return True
    if re.search(r'[bcdfghjklmnpqrstvwxz]{5,}', token, re.I):
        return True
    inner_caps = sum(1 for ch in token[1:] if ch.isupper())
    return inner_caps >= 3


def signals(account, burst_ids):
    """The bot signals for one inactive account, strongest first."""
    out = []
    name = account.get('name') or ''
    domain = (account.get('email') or '').rsplit('@', 1)[-1].lower()
    if re.search(r'https?://|www\.|\.(com|net|org|ru|xyz|top|io)\b', name, re.I):
        out.append('link_in_name')
    if _looks_random(name):
        out.append('random_name')
    if domain in DISPOSABLE_DOMAINS:
        out.append('throwaway_email')
    if account['id'] in burst_ids:
        out.append('burst')
    return out


def burst_ids(accounts):
    """Ids of inactive accounts made in bursts (BURST_SIZE or more within
    BURST_WINDOW), the classic shape of a bot run."""
    ordered = sorted(accounts, key=lambda a: a['created_at'])
    found, start = set(), 0
    for end in range(len(ordered)):
        while ordered[end]['created_at'] - ordered[start]['created_at'] > BURST_WINDOW:
            start += 1
        if end - start + 1 >= BURST_SIZE:
            found.update(a['id'] for a in ordered[start:end + 1])
    return found


def review(accounts):
    """Every inactive account with its signals, strong suspects first. A
    burst alone isn't strong (a team signing up together at practice looks
    the same), so it takes a link, a random name or a throwaway email."""
    bursts = burst_ids(accounts)
    rows = []
    for a in accounts:
        s = signals(a, bursts)
        rows.append(dict(a, signals=s, strong=any(x != 'burst' for x in s)))
    return sorted(rows, key=lambda r: (not r['strong'], -len(r['signals']), r['created_at']))
