"""Checks that the scorer accepts format differences and rejects value differences."""
from score import same_money, same_date, same_duration, same_law, norm_org, norm_id, items_match, txns_match, check

cases = [
 ("money fmt", same_money("$2,600.00", "2600"), True),
 ("money IDR thousands", same_money("60.000", "60000"), True),
 ("money euro comma", same_money("889,20", "889.20"), True),
 ("money czk", same_money("60.000,- Kc", "60000"), True),
 ("money wrong digit", same_money("8,010.42", "6,010.42"), False),
 ("money wrong cents", same_money("521.04", "521.00"), False),
 ("date us 2-digit", same_date("02-06-89", "1989-02-06", False), True),
 ("date us wrong", same_date("02-06-89", "1989-06-02", False), False),
 ("date dayfirst", same_date("15/01/2019", "2019-01-15", True), True),
 ("date czech", same_date("9.1.1995", "1995-01-09", True), True),
 ("date text", same_date("October 22, 2001", "2001-10-22", False), True),
 ("date canada", same_date("Dec 14/90", "1990-12-14", False), True),
 ("duration", same_duration("12 months", "twelve months"), True),
 ("duration diff", same_duration("90 days", "60 days"), False),
 ("law", same_law("Delaware", "the State of Delaware"), True),
 ("law wrong", same_law("Delaware", "New York"), False),
 ("org suffix", norm_org("Hazleton Laboratories America, Inc.") == norm_org("HAZLETON LABORATORIES AMERICA INC"), True),
 ("org different", norm_org("Hazleton Laboratories America, Inc.") == norm_org("Hazleton Laboratories"), False),
 ("id dashes", norm_id("930-70-2096") == norm_id("930702096"), True),
 ("null required", check("id", {}, None, "12345", "x")[0], False),
 ("null ok", check("id", {}, None, None, "x")[0], True),
 ("items exact", items_match([{"name":"TICKET CP","price":"60.000"}],[{"name":"-TICKET CP","price":"60,000"}])==(1,1,1), True),
 ("items extra", items_match([{"name":"A","price":"1"}],[{"name":"A","price":"1"},{"name":"B","price":"2"}])[2]==2, True),
 ("perpetual ok", check("date", {"dayfirst": False}, "Perpetual", "perpetual", "x")[0], True),
 ("perpetual vs date", check("date", {"dayfirst": False}, "perpetual", "3/29/19", "x")[0], False),
 ("any-of match", check("text", {}, {"any": ["PERMAS JAY", "PERMAS JAYA"]}, "Permas Jaya", "x")[0], True),
 ("any-of null ok", check("id", {}, {"any": [None, "22740"]}, None, "x")[0], True),
 ("any-of miss", check("id", {}, {"any": [None, "22740"]}, "22741", "x")[0], False),
]
T=[{"date":"2024-01-01","debit":None,"credit":10.0,"balance":110.0},{"date":"2024-01-02","debit":5.0,"credit":None,"balance":105.0},{"date":"2024-01-03","debit":1.0,"credit":None,"balance":104.0}]
cases += [
 ("txns exact", txns_match(T,[{"date":"2024-01-01","debit":None,"credit":10,"balance":110},{"date":"2024-01-02","debit":5,"credit":None,"balance":105}])["exact"], True),
 ("txns skipped row", txns_match(T,[{"date":"2024-01-01","debit":None,"credit":10,"balance":110},{"date":"2024-01-03","debit":1,"credit":None,"balance":104}])["exact"], False),
 ("txns wrong amount", txns_match(T,[{"date":"2024-01-01","debit":None,"credit":11,"balance":110}])["exact"], False),
]
bad = [(n,g,w) for n,g,w in cases if bool(g)!=w]
for n,g,w in cases: print(("PASS" if bool(g)==w else "FAIL"), n)
print(len(cases)-len(bad), "/", len(cases), "passed")
