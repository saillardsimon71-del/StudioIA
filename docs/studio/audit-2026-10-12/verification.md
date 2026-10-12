# Vérification avant publication - 12 octobre 2026

Version locale préparée depuis `e90f9e4`, avec les modifications du studio et les six nouveaux fichiers de tests. Les commandes ci-dessous ont été exécutées sur cette version, avant publication de la branche `codex/studioia-reliability`.

## Résultats

- Backend complet : **1388 tests réussis, 2 ignorés**, 527,16 secondes. Deux avertissements de fermeture de sous-processus/asyncio sous Windows, sans échec de test.
- Interface, `npm run check` : **0 erreur**, 11 avertissements dans 5 fichiers.
- Interface, `npm test` : vérifications storage SSR, transmission des valeurs et édition des plans réussies.
- Interface, `npm run build` : compilation et génération du site réussies.
- `git diff --cached --check` : aucune erreur.
- Les snapshots JSON du dossier d'audit ont été relus par le parseur JSON.

## Reproduire

Depuis `calliope-backend`, avec les dépendances de développement installées :

```powershell
$env:PYTHONPATH='src'
..\.venv\Scripts\python.exe -m pytest tests -q
```

Depuis `calliope-web` :

```powershell
npm run check
npm test
npm run build
```

## Portée

Ces résultats vérifient les comportements logiciels exercés par les tests. Ils ne certifient pas la qualité des générations distantes, l'intelligibilité des voix, leur attribution, les raccords visuels ni la rétention des spectateurs. Aucun nouveau rendu payant n'a été lancé pour publier le dépôt. Les cas audiovisuels et les contournements restent explicitement ouverts à l'audit.
