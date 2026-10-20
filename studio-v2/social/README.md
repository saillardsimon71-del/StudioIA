# X et funnel Studio IA V2

Un seul scheduler : préparation de la file, publication X API, DM demandés et inscription des contacts confirmés à la séquence Kit. Aucun composant de V1 n'est utilisé.

## Ce qui est prêt et ce qui manque

Le code et le PDF sont livrés. La publication réelle est désactivée tant que les identifiants ne sont pas configurés. La landing n'est pas automatiquement déployée sur fruitcity.sitequivend.fr. Le site existant ne propose pas de paiement à 197 EUR ; `STUDIO_CHECKOUT_URL` doit désigner cette offre précise.

La connexion X d'un navigateur ne fournit pas des jetons API. Il faut une application X avec les permissions lecture, écriture, messages privés et des jetons OAuth 1.0a utilisateur régénérés après modification des permissions. Les frais dépendent de l'offre et de la consommation du compte développeur ; aucune facture de 200 USD ou de 0,98 USD n'est présumée.

Au 20 octobre 2026, les règles [Original Content Rewards](https://help.x.com/en/using-x/original-content-rewards) excluent le contenu créé ou publié automatiquement. Cette automation sert le funnel commercial ; elle ne garantit ni éligibilité, ni revenus X. Le label IA ne change pas cette exclusion. Les [règles d'automation X](https://help.x.com/en/rules-and-policies/x-automation) imposent aussi le consentement aux DM et interdisent les scripts de publication sur le site.

## Source et file

- Tous les plans de l'épisode doivent être validés humainement. Le dernier export doit avoir un contrôle qualité réussi et correspondre exactement au projet validé.
- Les anciens exports sans empreinte de projet doivent être réexportés une fois. Une validation révoquée ou un nouvel export annule les anciens posts en attente.
- Quatre posts par export : prompt abrégé, teaser réel de cinq secondes, méthode, ressource. Le prompt complet reste dans la file. Pas de vues inventées ; un coût absent ou importé est indiqué comme non mesuré.
- `data/x_queue.json` est verrouillé et remplacé atomiquement. Les créneaux sont 09:00, 14:00 et 18:00 à Paris avec un décalage mémorisé de 5 à 15 minutes. Le quatrième post passe au jour suivant. Le changement d'heure est pris en charge.
- Un passage publie au plus un post. Limite de quatre transmissions confirmées ou incertaines par date de Paris, dix minutes minimum entre transmissions. Les créneaux ratés depuis plus de trente minutes sont replanifiés, sans rattrapage massif.
- Une intention est sauvegardée avant chaque envoi. Un timeout après transmission ou une erreur serveur marque le post `uncertain`. Il ne repart jamais automatiquement. Vérifier le compte X puis rapprocher son identifiant dans la file, scheduler arrêté, avant de décider de le remettre en attente.
- `data/today_posts.txt` donne le texte et le prompt complet en cas d'indisponibilité. Aucun changement automatique de transport.

Les hooks de validation/export dans l'application préparent seulement la file. Ils ne publient pas sur X.

## Installation

Depuis la racine du dépôt, avec Python 3.11 :

```powershell
python -m pip install -r studio-v2/social/requirements.txt
python studio-v2/social/x_scheduler.py --data-dir C:/StudioIA/data
```

Utiliser le dossier réel de V2 contenant les exports et `v2.db`. Conserver ce dossier hors du checkout d'un runner Actions et le sauvegarder avec ses médias. Ne pas créer une base vide pour la production.

Copier `social.env.example` dans ce dossier privé sous le nom `social.env`. Renseigner les quatre secrets X et les paramètres Kit. Ce fichier reste hors de Git ; limiter son accès au compte qui exécute le scheduler. Aucune clé dans un post, un log ou un argument de commande.

## Kit : cinq emails et double confirmation

1. Créer un formulaire Kit dédié. Activer son email de confirmation et exiger le clic de confirmation. Utiliser un expéditeur validé.
2. Renseigner `KIT_API_KEY`, `KIT_FORM_ID`, `KIT_SENDER_EMAIL`, `STUDIO_LANDING_URL`, `STUDIO_CHECKOUT_URL` et `STUDIO_PRIVACY_URL` dans le fichier privé. L'URL de landing doit finir par `/funnel`.
3. Créer la séquence depuis le texte versionné dans `funnel.json` :

```powershell
powershell -NoProfile -File studio-v2/social/run-social.ps1 -DataDir C:/StudioIA/data -ConfigureKit
```

La commande crée une séquence dédiée, non répétable, avec cinq emails publiés, puis mémorise ses identifiants dans SQLite. Elle reprend une configuration partielle sans recréer les emails confirmés. Les délais relatifs sont 0, 1, 1, 1, 1 jour. Le scheduler vérifie la confirmation des nouveaux contacts toutes les dix minutes et les inscrit ensuite à la séquence. Kit gère l'envoi et la désinscription. Un abonnement existant annulé n'est pas réactivé par le script.

`KIT_SEQUENCE_ID` est facultatif sur le même dossier de données après cette initialisation. Pour un déploiement séparé, renseigner explicitement l'identifiant retourné et utiliser la même base V2. Vérifier une inscription avec une adresse de test que tu contrôles avant ouverture publique.

La première campagne peut proposer de répondre exactement STUDIO sous son propre post pour demander un seul DM automatique. Les mentions hors de ce post, les simples occurrences du mot, le compte lui-même et les doublons ne déclenchent rien. STOP en DM bloque durablement les nouvelles livraisons. Les épisodes suivants renvoient directement à la landing, sans répéter le CTA à mot-clé. Aucun follow, unfollow ou repost automatique.

## Serveur public

Charger les variables du service, dont `STUDIO_V2_DATA_DIR` et les paramètres Kit, puis :

```powershell
python -m uvicorn social.funnel_server:app --app-dir studio-v2 --host 127.0.0.1 --port 8271
```

Exposer uniquement cette application derrière HTTPS : `/funnel`, `/funnel/assets/*`, `/funnel/bible.pdf` et `/api/funnel/*`. Ne pas exposer l'API d'administration du Studio. La landing demande explicitement l'accord aux cinq emails et affiche l'état de configuration réel ; un fournisseur absent produit une indisponibilité, jamais une fausse inscription réussie.

Le serveur et le scheduler doivent partager le même dossier SQLite sur la même machine. Sur un autre domaine que le site actuel, remplacer aussi les liens de mentions légales et de confidentialité du HTML. Le PDF contient les quatre sources originales : `00_showrunner.md`, `01_attention.md`, `02_retention.md`, `03_narration.md`.

```powershell
python studio-v2/social/build_lead_magnet.py
powershell -NoProfile -File studio-v2/social/run-social.ps1 -DataDir C:/StudioIA/data -Doctor
```

Le diagnostic ne publie rien. Il vérifie les paramètres requis puis la landing HTTPS, la disponibilité de l'inscription, l'URL de paiement annoncée et le PDF public. Il retourne un code non nul si l'activation est incomplète. Le post de funnel reste bloqué tant que ces contrôles échouent. Un compte ou un droit API incorrect est également bloqué avant transmission.

## Choisir un seul planning

### Windows local

```powershell
powershell -NoProfile -File studio-v2/social/install-scheduler.ps1 -DataDir C:/StudioIA/data
```

Cette commande exige un diagnostic réussi avant d'installer `StudioIA-V2-Social`, toutes les dix minutes, sans fenêtre. Le compte Windows doit rester connecté et la machine allumée. Les exécutions ne se chevauchent pas. Pour tester sans publier, utiliser `run-social.ps1 -DataDir ... -DryRun`. Pour arrêter : `Disable-ScheduledTask -TaskName StudioIA-V2-Social`.

### GitHub Actions

`x_post.yml` vérifie la branche à chaque push. La publication exige un runner persistant étiqueté `studio-v2-social`, les secrets X et `KIT_API_KEY`, les variables Kit/URLs/dossier de données et `X_AUTOMATION_ENABLED=true`.

Attention : [GitHub exécute les schedules depuis la branche par défaut uniquement](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule). Un push sur `arena/studio-ia-v2-rebuild` ne suffit donc pas à activer le cron. Le workflow doit aussi être présent sur la branche par défaut. Le runner doit avoir accès à la vraie base et aux exports, pas à une copie éphémère. Les retards Actions peuvent décaler un créneau ; le scheduler évite tout rattrapage en rafale.

Le workflow n'écrit ni la file, ni les contacts dans Git et ne les dépose pas en artifacts Actions. Ne pas cumuler le planning Windows et Actions sur la même installation.

## Transport navigateur demandé

`x_browser.py` contient une option Playwright séparée, désactivée par défaut et jamais utilisée en secours automatique. X interdit actuellement cette méthode. Elle n'a pas été testée sur une session X connectée ; aucun navigateur connecté n'était disponible pendant l'implémentation.

Un opérateur qui décide de l'utiliser doit installer `requirements-browser.txt` et Chromium, vérifier les sélecteurs du compositeur et du label IA, définir `X_ALLOW_UNSUPPORTED_BROWSER=1` et `X_BROWSER_AI_LABEL_SELECTOR`, puis créer sa session avec `x_browser.py --data-dir ...`. Le lancement se fait explicitement par `x_scheduler.py --publish --backend browser`. Les DM restent exclusivement API. Pas de contournement de CAPTCHA ou de masquage du navigateur.

## Vérifications

```powershell
python -m pip install -r studio-v2/social/requirements-test.txt
$env:MODAL_CONFIG_PATH = Join-Path $env:TEMP 'studio-social-empty-modal.toml'
python -m pytest studio-v2/tests -q
```

L'épisode fictif utilise une vraie vidéo FFmpeg de six secondes, une base isolée et un serveur HTTP local simulant X. Le test vérifie quatre posts, l'upload du teaser, un seul DM sur demande explicite et sa persistance. Les autres tests couvrent le changement d'heure, les erreurs ambiguës, les doublons, STOP, les validations révoquées, le plafond journalier et le parcours Kit. Aucune publication réelle ou dépense GPU pendant les tests.
