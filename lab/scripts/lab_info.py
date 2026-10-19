"""Print lab facts for setup.sh (bash cannot read YAML)."""
import sys

import common as c

what = sys.argv[1]
if what == "team-folders":          # folder<TAB>group<TAB>read|write
    for folder, perms in c.C["team_folders"].items():
        for g, p in perms.items():
            print(f"{folder}\t{g}\t{p}")
elif what == "people":
    print("\n".join(c.PEOPLE))
elif what == "domain":
    print(c.DOMAIN)
elif what == "base-dn":
    print(c.BASE_DN)
