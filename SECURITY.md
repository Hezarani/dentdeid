# Security & privacy leaks

DentDeID is a privacy tool, so a **missed identifier is a security issue**.

If you find a case where DentDeID leaves identifying information in its output (metadata, burned-in
text, file names, faces, reports), please report it privately through GitHub's **private
vulnerability reporting**: on the repository page open **Security → Report a vulnerability**.

Please **do not attach real patient data**. Describe the vendor/software, modality, where the
identifier was, and — ideally — reproduce it with `dentdeid synth` or a hand-made synthetic image.

We aim to acknowledge reports within 7 days. Confirmed leaks are fixed with a regression test in
`tests/test_audit_regressions.py`.
