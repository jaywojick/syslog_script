For syslog_report_view.py

Built-in table:

python3 syslog_report_view.py <final_assessment.json> --workgroup <worker groupname>

Customer CSV:

python3 syslog_report_view.py <final_assessment.json> --workgroups-file <workgroups.csv> --workgroup <worker group>

Example:

python3 syslog_report_view.py syslog_xesplfwdm6p_..._final_assessment.json --workgroup prod-cvs-woon-streaming-s2s
python3 syslog_report_view.py syslog_xesplfwdm6p_..._final_assessment.json --workgroups-file workgroups.csv --workgroup prod-cvs-woon-streaming-s2s

Both save <server>_results.txt next to the JSON.
