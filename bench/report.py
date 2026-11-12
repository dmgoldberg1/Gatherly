import argparse
import json
from pathlib import Path

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    parser.add_argument('--report')
    parser.add_argument('--config-only', action='store_true')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text())
    lines = ['## Gatherly load benchmark', '', '```json', json.dumps(config, ensure_ascii=False, indent=2), '```', '']
    if args.config_only:
        pass
    elif args.report and Path(args.report).is_file():
        report = json.loads(Path(args.report).read_text())
        if report['config'] != config:
            raise ValueError('Report configuration differs from the effective run configuration')
        lines += ['| Scenario | Target RPS | Successful RPS | p95 ms | p99 ms | Error rate | Dropped iterations |', '| --- | ---: | ---: | ---: | ---: | ---: | ---: |']
        for name, values in report['scenarios'].items():
            def rounded(value):
                return round(value, 3) if value is not None else None
            lines.append('| ' + ' | '.join(str(x) for x in (name, values['target_rps'], rounded(values['successful_rps']), rounded(values['p95_ms']), rounded(values['p99_ms']), values['error_rate'], values['dropped_iterations'])) + ' |')
    else:
        lines.append('The run did not produce a k6 report. See diagnostic artifacts and job logs.')
    with Path(args.output).open('a') as stream:
        stream.write('\n'.join(lines) + '\n')


if __name__ == "__main__":
    main()
