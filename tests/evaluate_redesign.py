"""Score reviewer-annotated outputs on the distributable evaluation cases."""
import argparse
import json
import statistics
from pathlib import Path

CASES = Path(__file__).parent / 'fixtures' / 'redesign_evaluation.json'


def score(cases, observations):
    if set(observations) != {case['case'] for case in cases}:
        raise ValueError('Provide one observation for each case.')
    recalled, routed, claims, unsupported, times = 0, 0, 0, 0, []
    for case in cases:
        row = observations[case['case']]
        expected_fields = {'recalled', 'scope', 'response', 'claims_checked',
                           'unsupported_claims', 'correction_seconds'}
        if not isinstance(row, dict) or row.keys() != expected_fields or type(row['recalled']) is not bool:
            raise ValueError('Use the annotation fields from --annotation-template.')
        checked, invalid = row['claims_checked'], row['unsupported_claims']
        seconds = row['correction_seconds']
        if (type(checked) is not int or type(invalid) is not int or not 0 <= invalid <= checked
                or (seconds is not None and (type(seconds) not in (int, float) or not 0 <= seconds < float('inf')))):
            raise ValueError('Supply valid claim counts and a nonnegative measured duration or null.')
        recalled += row['recalled']
        routed += row['recalled'] and (row['scope'], row['response']) == (case['scope'], case['response'])
        claims += checked
        unsupported += invalid
        if seconds is not None:
            times.append(seconds)
    return dict(expected=len(cases), recalled=recalled, routed_correctly=routed,
                claims_checked=claims, unsupported_claims=unsupported, timed_cases=len(times),
                median_active_correction_seconds=statistics.median(times) if times else None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    options = parser.add_mutually_exclusive_group(required=True)
    options.add_argument('--annotation-template', action='store_true')
    options.add_argument('--observations', type=Path)
    args = parser.parse_args()
    cases = json.loads(CASES.read_text())
    if args.annotation_template:
        result = {case['case']: dict(recalled=False, scope=None, response=None, claims_checked=0,
                                     unsupported_claims=0, correction_seconds=None) for case in cases}
    else:
        result = score(cases, json.loads(args.observations.read_text()))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
