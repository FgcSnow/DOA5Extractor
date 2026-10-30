import json, sys

JOBS = {'classic-archive-990015': 0, 'v2-archive-990016': 1, 'costumes-particules-common': 2}
recette = json.load(open(sys.argv[1], encoding='utf-8-sig'))
lignes = []
for job in recette['jobs']:
    if job['id'] in JOBS:
        for o in job['outputs']:
            lignes.append((JOBS[job['id']], o['path'], o['size'], o['sha256']))
lignes.sort()
if len({j for j, *_ in lignes}) != 3 or len(lignes) != 9:
    sys.exit('recette inattendue : %d fichiers (9 attendus, jeux 990015, 990016 et particules)' % len(lignes))
print('/* doa5installer.c, recette %s */' % recette['id'])
for j, p, s, h in lignes:
    print('    {%d, "%s", %du, "%s"},' % (j, p, s, h))
print('\n# doa5installer.py, recette %s' % recette['id'])
for j, p, s, h in lignes:
    print("    '%s': (%d, %d, '%s')," % (p, j, s, h))
