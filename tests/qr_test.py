#!/usr/bin/env python3
"""Decodes QR codes from public/qr.js with OpenCV's reader: every version qr.js
supports and every mask. Needs node, numpy and opencv-python-headless:
    pip install numpy opencv-python-headless && python3 tests/qr_test.py"""
import json, os, subprocess, sys
import numpy as np
import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
QR = os.path.abspath(os.path.join(HERE, '..', 'public', 'qr.js'))
texts = ['HELLO', 'https://meet.example.com/j/ABC234', 'https://meet.conklin.example/j/Q7KM9P',
         'x' * 60, 'é中文' * 10, 'y' * 150, 'z' * 210]
detector = cv2.QRCodeDetector()
bad = 0
for t in texts:
    for mask in list(range(8)) + [None]:
        js = subprocess.run(['node', '-e', 'const q=require(process.argv[1]);const m=process.argv[3];'
                             'process.stdout.write(JSON.stringify(q.matrix(process.argv[2], m==="auto"?undefined:+m)))',
                             QR, t, 'auto' if mask is None else str(mask)], capture_output=True, text=True, check=True)
        q = json.loads(js.stdout)
        mod = np.array(q['modules'], dtype=np.uint8)
        img = np.pad(1 - mod, 4, constant_values=1) * 255          # dark modules black, quiet zone
        img = cv2.resize(img.astype(np.uint8), None, fx=8, fy=8, interpolation=cv2.INTER_NEAREST)
        got, _, _ = detector.detectAndDecode(img)
        if got != t:
            bad += 1
            print('FAIL', repr(t[:24]), 'version', q['version'], 'mask', q['mask'], 'decoded', repr(got[:24]))
print('qr.js: %s' % ('every code decodes' if not bad else '%d failures' % bad))
sys.exit(1 if bad else 0)
