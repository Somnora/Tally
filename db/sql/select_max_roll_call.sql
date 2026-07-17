SELECT COALESCE(MAX(roll_call_number), 0)
FROM voting_records
WHERE chamber = %(chamber)s AND congress = %(congress)s AND session = %(session)s
