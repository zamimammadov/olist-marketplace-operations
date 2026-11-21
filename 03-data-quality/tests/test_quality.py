import csv
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from quality.checks import CONTROL_HEADER, METRICS, ORDER_HEADER, run_checks
from quality.__main__ import run_gate
from quality.inject import inject

A = ['a'*32,'2017-01-02','SP','delivered','2017-01-08','2017-01-08',1,1,1300,200,1500]
B = ['b'*32,'2017-02-03','AM','shipped','','2017-02-28',2,1,2200,130,2330]
CA = ['2017-01',1,1,1,1300,200,1500]
CB = ['2017-02',1,2,1,2200,130,2330]


def csv_bytes(header, rows):
    f = io.StringIO(newline='')
    w = csv.writer(f, lineterminator='\n')
    w.writerow(header)
    w.writerows(rows)
    return f.getvalue().encode()


def check(rows, controls):
    return run_checks(csv_bytes(ORDER_HEADER, rows), csv_bytes(CONTROL_HEADER, controls))


class Rules(unittest.TestCase):
    def test_baseline_and_denominator(self):
        r = check([A,B],[CA,CB]); self.assertFalse(r.findings)
        self.assertEqual(r.profile["checks_compared"], 12)
        self.assertEqual(r.profile["on_time_rows"],1)
        self.assertEqual(r.profile["eligible_delivered_rows"],1)

    def test_late_is_outcome_only(self):
        row=A.copy(); row[4]="2017-01-11"
        r=check([row],[CA]);self.assertFalse(r.findings)
        self.assertEqual(r.profile["late_rows"],1)

    def test_missing_delivered_evidence_is_warning(self):
        row=A.copy();row[4]=""
        r=check([row],[CA]);self.assertEqual(r.counts,{"DELIVERY_COVERAGE":1})
        self.assertEqual(r.profile["eligible_delivered_rows"],0)

    def test_valid_source_anomalies_visible(self):
        row=A.copy();row[4]="2016-12-31";row[-1]=1510
        control=CA.copy();control[-1]=1510
        r=check([row],[control])
        self.assertEqual(r.counts,{"VALUE_VARIANCE":1,"ORDER_CHRONOLOGY":1})
        self.assertEqual(r.profile["payment_gap_gross_cents"],10)

    def test_even_offsetting_months_are_detected(self):
        a=A.copy();b=B.copy();a[-1]+=2;b[-1]-=2
        r=check([a,b],[CA,CB])
        self.assertEqual(r.counts["CONTROL_TOTAL"],2)
        self.assertEqual(r.profile["payment_gap_gross_cents"],4)

    def test_duplicate_tracks_source_line_and_changes_sums(self):
        r=check([A,B,A],[CA,CB])
        hit=next(f for f in r.findings if f["rule_id"]=="DUPLICATE_ORDER")
        self.assertEqual((hit["source_line"],hit["observed"]),(4,"Repeated from line 2"))
        self.assertEqual(r.counts["CONTROL_TOTAL"],6)

    def test_three_contract_failures_on_single_row(self):
        row=A.copy();row[0]="invalid";row[1]="2017-02-29";row[9]="1.10"
        r=check([row],[CA])
        self.assertEqual(r.counts,{"ORDER_ID":1,"ORDER_DATE":1,"ORDER_MEASURE":1,"CONTROL_SCOPE":1})
        self.assertEqual(r.profile["typed_order_rows"],0)

    def test_zero_child_count_with_nonzero_amount(self):
        row=A.copy();row[6]=0;control=CA.copy();control[2]=0
        self.assertEqual(check([row],[control]).counts,{"CHILD_TOTAL":2})

    def test_control_contract_and_missing_period(self):
        invalid=CB.copy();invalid[1]="NaN"
        r=check([A,B],[CA,CA,invalid])
        self.assertEqual(r.counts,{"CONTROL_ROW":2,"CONTROL_SCOPE":1})

    def test_invalid_utf8_multiline_or_empty_input(self):
        for blob in [b'bad\n', b'\xff', b'"abc']:
            with self.subTest(blob=blob):
                self.assertGreater(run_checks(blob,csv_bytes(CONTROL_HEADER,[CA])).counts['FILE_SCHEMA'],0)
        self.assertEqual(check([],[]).counts, {"EMPTY_FILE":2})

    def test_scope_missing_and_unexpected(self):
        r=check([A],[CB]); self.assertEqual(r.counts,{"CONTROL_SCOPE":2})

    def test_header_width_error(self):
        r=check([A[:-1]],[CA]); self.assertEqual(r.counts,{"FILE_SCHEMA":1,"CONTROL_SCOPE":1})

    def test_non_delivered_with_date_warns(self):
        row=B.copy(); row[4]='2017-02-14'
        self.assertEqual(check([row],[CB]).counts,{"DELIVERY_COVERAGE":1})


class Evidence(unittest.TestCase):
    def test_publishing_faults_and_recovery(self):
        with tempfile.TemporaryDirectory() as root:
            root=Path(root); raw=root/'orders.csv'; controls=root/'ctrl.csv'; bad=root/'bad.csv'; out=root/'out'
            third=A.copy();third[0]='c'*32; ca=[CA[0]]+[2*x for x in CA[1:]]
            baseline=csv_bytes(ORDER_HEADER,[A,B,third]); raw.write_bytes(baseline)
            controls.write_bytes(csv_bytes(CONTROL_HEADER,[ca,CB]))
            before=run_gate(raw,controls,out)
            self.assertEqual(before['decision'],'PASS')
            with self.assertRaises(ValueError):inject(raw,raw)
            inject(raw,bad)
            with self.assertRaises(FileExistsError):inject(raw,bad)
            damaged=run_gate(bad,controls,out)
            self.assertEqual(damaged['decision'],'HOLD')
            self.assertTrue({'DUPLICATE_ORDER','ORDER_DOMAIN','CONTROL_TOTAL'} <= set(damaged['rule_counts']))
            self.assertEqual(raw.read_bytes(),baseline)
            restored=run_gate(raw,controls,out)
            self.assertEqual(before,restored)
            self.assertEqual(len(list((out/'runs').iterdir())),2)
            latest=json.loads((out/'latest.json').read_text())
            self.assertEqual(latest['run_id'],before['run_id'])
            f=out/'runs'/damaged['run_id']/'findings.csv'
            self.assertTrue(f.read_text().startswith("rule_id,severity,"))
            def call(path):
                return subprocess.run([sys.executable,'-m','quality','--input',str(path),'--controls',str(controls),
                                       '--out',str(out)],capture_output=True).returncode
            self.assertEqual(call(raw),0);self.assertEqual(call(bad),1);self.assertEqual(call(root/'missing'),2)
            self.assertEqual(json.loads((out/'latest.json').read_text())['run_id'],damaged['run_id'])


if __name__=='__main__':
    unittest.main()
