"""Clean an Artificial Analysis model export without changing the source file.

Usage: python3 clean_model_data.py /path/to/model_data_complete.csv
Only Python's standard library is required. Outputs go to data/processed by default.
Missing numbers remain blank; different model versions/settings stay separate.
"""

import argparse
import csv
import json
import math
import re
from collections import Counter
from datetime import date
from pathlib import Path


TEXT_COLUMNS = {'id', 'name', 'slug', 'model_creator.id', 'model_creator.name'}
PLACEHOLDERS = {'', 'null', 'none', 'nan', 'n/a'}
REQUIRED_COLUMNS = ['model_id', 'model_name', 'release_date', 'creator_id',
                    'creator_name', 'intelligence_index']


def column_name(name):
    aliases = {
        'id': 'model_id', 'name': 'model_name', 'slug': 'model_slug',
        'model_creator.id': 'creator_id', 'model_creator.name': 'creator_name',
        'artificial_analysis_intelligence_index_cost.total_cost': 'intelligence_total_cost',
        'artificial_analysis_intelligence_index_cost.cost_per_task.total_cost': 'intelligence_cost_per_task',
    }
    name = aliases.get(name, name.replace('evaluations.artificial_analysis_', ''))
    return re.sub(r'[^a-z0-9]+', '_', name.lower()).strip('_')


def clean(input_path, output_dir):
    input_path, output_dir = Path(input_path), Path(output_dir)
    output_names = ['models_clean.csv', 'models_with_benchmarks.csv',
                    'cleaning_issues.csv', 'cleaning_report.json']
    if input_path.resolve() in [(output_dir / n).resolve() for n in output_names]:
        raise ValueError('Input must be different from every output file.')
    with input_path.open(encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames
        if not headers or len(headers) != len(set(headers)):
            raise ValueError('Missing or duplicate column headers.')
        if not {'id', 'name', 'slug', 'release_date'}.issubset(headers):
            raise ValueError('Expected model export columns are missing.')
        raw = list(reader)
    if any(None in row or any(v is None for v in row.values()) for row in raw):
        raise ValueError('CSV contains rows with the wrong number of fields.')

    # Normalize blank markers, but preserve internal model-name punctuation.
    rows = [{k: ('' if v.strip().lower() in PLACEHOLDERS else v.strip())
             for k, v in row.items()} for row in raw]
    empty_columns = [k for k in headers if rows and all(not row[k] for row in rows)]
    keep = [k for k in headers if k not in empty_columns]
    names = [column_name(k) for k in keep]
    if not set(REQUIRED_COLUMNS).issubset(names):
        raise ValueError('A required column is absent or entirely empty.')
    if len(names) != len(set(names)):
        raise ValueError('Column renaming would produce duplicate names.')

    issues, cleaned, seen = [], [], set()
    duplicates_removed = 0
    for line, row in enumerate(rows, start=2):
        signature = tuple(row[k] for k in headers)
        if signature in seen:
            duplicates_removed += 1
            continue
        seen.add(signature)
        result = {}
        for key in keep:
            value = row[key]
            reason = None
            if key == 'release_date' and value:
                try:
                    value = date.fromisoformat(value).isoformat()
                except ValueError:
                    reason = 'Invalid ISO date; cleared'
            elif key not in TEXT_COLUMNS and key != 'release_date' and value:
                try:
                    number = float(value)
                    if not math.isfinite(number) or number < 0:
                        raise ValueError
                    value = number
                except ValueError:
                    reason = 'Invalid/nonfinite/negative numeric value; cleared'
            if reason:
                issues.append({'source_line': line, 'model_id': row['id'],
                               'column': key, 'original_value': row[key], 'issue': reason})
                value = ''
            result[column_name(key)] = value
        if not row['id']:
            issues.append({'source_line': line, 'model_id': '', 'column': 'id',
                           'original_value': '', 'issue': 'Missing model ID; row retained'})
        cleaned.append(result)

    # Keep a row only when every requested field is populated and valid.
    missing_required = [r for r in cleaned
                        if any(r[k] == '' for k in REQUIRED_COLUMNS)]
    cleaned = [r for r in cleaned
               if all(r[k] != '' for k in REQUIRED_COLUMNS)]

    # Repeated IDs with different data require review, not arbitrary deletion.
    id_counts = Counter(r['model_id'] for r in cleaned if r['model_id'])
    conflicts = {k: v for k, v in id_counts.items() if v > 1}
    benchmark_columns = [column_name(k) for k in keep if k.startswith('evaluations.')]
    benchmark_rows = [r for r in cleaned if any(r[k] != '' for k in benchmark_columns)]
    output_dir.mkdir(parents=True, exist_ok=True)

    def write_csv(filename, records, fields):
        with (output_dir / filename).open('w', encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(records)

    write_csv('models_clean.csv', cleaned, names)
    write_csv('models_with_benchmarks.csv', benchmark_rows, names)
    write_csv('cleaning_issues.csv', issues,
              ['source_line', 'model_id', 'column', 'original_value', 'issue'])
    report = {
        'source': str(input_path.resolve()), 'input_rows': len(raw),
        'output_rows': len(cleaned), 'benchmark_rows': len(benchmark_rows),
        'exact_duplicates_removed': duplicates_removed,
        'missing_required_rows_removed': len(missing_required),
        'required_columns': REQUIRED_COLUMNS,
        'missingness_rule': 'Remove rows missing any required field; other missing fields are allowed.',
        'empty_columns_removed': empty_columns, 'column_mapping': dict(zip(keep, names)),
        'conflicting_model_ids_retained': conflicts, 'invalid_values_flagged': len(issues),
        'missing_by_column': {k: sum(r[k] == '' for r in cleaned) for k in names},
        'policy': 'No imputation, fuzzy model merging, outlier removal, or score rescaling. '
                  'Median performance and v3 mean performance remain separate.',
    }
    (output_dir / 'cleaning_report.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('--output-dir', type=Path,
                        default=Path(__file__).resolve().parent / 'data' / 'processed')
    args = parser.parse_args()
    summary = clean(args.input, args.output_dir)
    print(f"Cleaned {summary['input_rows']} rows -> {summary['output_rows']} rows; "
          f"{summary['benchmark_rows']} have benchmark scores.")
    print(f"Removed {len(summary['empty_columns_removed'])} empty columns. "
          f"Flagged {summary['invalid_values_flagged']} issues.")
    print(f'Outputs: {args.output_dir.resolve()}')
