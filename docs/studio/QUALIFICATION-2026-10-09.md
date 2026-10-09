# Qualification StudioIA du 9 octobre 2026

Branche: codex/studioia-reliability. Point de départ: 24ddfd4173b5803efb708227f64294f50425dd00.

Cette livraison corrige le Studio pour les prochains épisodes. Aucun rendu, montage, manifest ou état de production du projet 11 n'a été modifié. Le SHA256 de son MP4 original reste 8db8eb1505f5644f3db22e1a730947cb6a7b283d2e3ef95d21bbcc828f131afd.

## Autopsie du premier épisode

Le projet 11, production 100, a bien produit un film: 71,041667 secondes, 18 plans, 720 x 1280, H.264/AAC, 24 fps. Le manifest initial contient 22 plans. Quatre plans ont été supprimés avant le succès final; les exports précédents échouaient à 80,62, 76,04 et 73,46 secondes utiles. Cela prouve le rendu réel complet, mais pas encore une durée de 65 secondes obtenue automatiquement pour chaque scénario.

Le montage retenu comporte 15 plans parlés sur 18. Les fenêtres de transcription conservées couvrent environ 58,26 s, soit 82 % du montage; ce sont des intervalles ASR, pas une mesure acoustique mot par mot. La durée médiane des plans est 3,708 s, avec une plage de 2 à 8 s. Le rythme est déjà variable et dense. Le bloc silencieux de révélation et les cuts imposés au changement de voix sont les corrections utiles; augmenter arbitrairement la fréquence des cuts ne serait pas justifié.

Les données originales et l'analyse détaillée sont conservées dans le dossier de mission outputs/qualification-episode11:
- autopsie-avant-correctifs.md: constats avant toute modification, tableau des 22 plans;
- detail-plan-par-plan.md: attendu, prompts exacts récupérables, transcription, QC, trim, appel Modal;
- evidence.json: snapshot persistant des jobs, takes, contextes, exports, audio et références;
- media-analysis.json: mesures FFmpeg des 22 fichiers bruts;
- image-prompt-*.txt, h3-prompt-*.txt, raw-shots-*.jpg: prompts et captures.

Les requêtes exactes, CallIDs et QC des quatre takes supprimées ont disparu par cascade SQL. Leur MP4 et leur job subsistent. L'heure et l'auteur des suppressions ne sont pas connus. Les stems audio temporaires et la commande exacte de mixage de ce run n'ont pas été conservés. Ces données manquantes ne sont pas reconstruites artificiellement.

## Causes et correctifs

| Défaut observé | Cause établie | Correction pour les nouveaux runs |
| --- | --- | --- |
| Ouvertures 1.1, 2.1, 3.1 sans son | Dialogue vide transformé en audio_mode=silent; soundscape=N/A; worker retirant la piste | Absence de parole distincte du silence complet. Ambiance/action natives par défaut, silence explicite toujours possible |
| Prise avec son annoncé mais sans piste acceptée | Fallback silencieux au montage | QC et export signalent la piste native manquante, sans substitution silencieuse |
| Cut à chaque changement de voix | Contrainte du planner/manifest | dialogue_turns attribués et ordonnés dans un même plan; IDs stables; contrôle de chaque réplique |
| Chiffres et ponctuation instables | Texte dessiné par les modèles | Valeur entière structurée, format commun dans le profil, panneau vide, texte exact en post-production |
| Musique absente et fond trop faible | music=none malgré la post-production; niveaux faibles avant gain | Identité musicale persistante; niveaux des stems mesurés; ducking et limiteur préservés |
| Bruitage sur le mauvais événement après suppression | Repérage par indice de montage | Cues et holds ancrés au clip_id; résolution de la position au moment du montage |
| Diagnostic perdu après suppression de plan | Cascade SQL des takes | Archive du plan, contexte, requêtes, métadonnées, jobs et télémétrie avant suppression |
| Export bloqué après des rendus valides | Durée native supérieure au budget écrit | Export sans padding ni coupure de parole, avec alerte de durée dans les métadonnées et l'interface |

Les défauts de son ne nécessitent aucune hypothèse de panne Modal. Les autres plans sans dialogue avaient le même comportement, même au milieu d'une scène.

## Contrat H3

Le [guide officiel MiniMax](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/docs/VIDEO_PROMPT_WRITING_GUIDE_base_en.md) décrit les IDs de locuteurs, les balises de dialogue, les descriptions séparées et les sections sonores. Il permet plusieurs locuteurs; aucune règle officielle ne limite une génération à une seule voix.

Le manifest garde les paroles en texte brut, séparées des locuteurs et des actions. Le compilateur seul produit les IDs (S1), (S2), les balises <d>[English] ...</d>, la première image et le soundscape. La langue provient de la série/du plan. La fin obligatoire conserve la langue de l'épisode.

Le profil de réalisation porte les constantes de cadrage, rythme, écoute, caméra, continuité, identité musicale et format numérique. Il ne raconte pas l'histoire et ne remplace pas la grammaire du moteur.

## Preuve réelle ciblée

Un seul nouveau rendu H3 a été lancé, séparément du projet 11:
- projet 13, clip 95, image 186, take vidéo 187, job 154;
- deux personnages, deux répliques: "You changed the score." puis "Look again. I won.";
- CallID fc-01M4GD0JRT8HB9ATHGFTQSZAT2;
- container ta-01M4GD0MF1KMYAA7JZKXXCA8GR;
- 6 secondes demandées, 6,583333 secondes brutes, 6 secondes conservées;
- transcription exacte des deux répliques, couverture 1,0 pour chacune, QC ok;
- aucun rerender; score 12,349 ajouté après génération, suivi de personnage accepté;
- MP4 720 x 1280, audio, musique discrète, événement sonore et ducking.

La première réplique occupe 0 -> 1,56 s; la seconde 2,56 -> 4,88 s. Les captures montrent les bouches actives dans l'ordre attendu. Elles ne constituent pas une mesure précise du lip-sync ni une garantie d'identité vocale sur toute une série.

Phases mesurées:
- soumission confirmée 13:18:04 UTC;
- entrée de fonction 13:18:10;
- préparation ComfyUI/modèle environ 48,6 s;
- sampler/artifact environ 68,1 s;
- résultat local validé 13:20:13;
- contrôle de parole initial environ 9 s;
- export final réussi.

Un incident de connexion du client de suivi a été contourné en reprenant le même appel. Aucune génération supplémentaire n'a été soumise. Le dossier outputs/qualification-probe6 contient result.json, measurements.json et les captures brutes/finales.

## Coût et débit

Les [tarifs Modal](https://modal.com/pricing), revérifiés le 9 octobre, sont 0,000542 USD/s pour L40S, 0,0000131 USD/core/s et 0,00000222 USD/GiB/s. Le déploiement actuel demande 8 cores, 96 GiB, min_containers=0, max_containers=2 et une extinction après 300 s. Le chargement et la période chaude sont facturables.

Le premier run cumule 547 s de jobs image et 958 s de jobs vidéo. Les 18 takes restantes attestent un même container et 614,456 s de génération distante. L'intervalle attesté entre ses appels est 874,862 s; le premier appel supprimé empêche une mesure complète du démarrage.

Hypothèse de budget à la configuration demandée: environ 1,10 USD de compute Modal pour un lot comparable, en incluant le démarrage estimé et 300 s de maintien à chaud. Ajouter 25 % de travail vidéo porte cette estimation vers 1,31 USD. Ce ne sont pas des montants facturés: il manque la facture, la consommation effective et les tarifs image/LLM Agnes.

| Volume sur 30 jours | Compute Modal indicatif | Avec +25 % de travail vidéo |
| --- | ---: | ---: |
| 3 épisodes/jour, 90 épisodes | 99 USD | 118 USD |
| 10 épisodes/jour, 300 épisodes | 330 USD | 393 USD |

Images, LLM, stockage, éventuels multiplicateurs et taxes sont exclus. Le temps séquentiel image + vidéo observé représente environ 25 minutes, hors écriture et assemblage. Les échanges dans un plan peuvent réduire le nombre de rendus, mais ce gain n'a pas encore été mesuré sur un nouvel épisode complet. La concurrence et le pool existants sont conservés; pas de multiplication aveugle des workers.

## Vérifications finales

- Suite complète locale: 1031 passed, 2 skipped, 2 warnings, 0 failed, en 437,37 secondes.
- Commande: ../.venv/Scripts/python.exe -m pytest --asyncio-mode=auto -q depuis calliope-backend, avec configuration de test isolée.
- Les deux warnings restent le nettoyage asyncio/Proactor Windows.
- 59 tests ciblés H3/qualification/overlay réussis; contrôle Ruff et git diff --check réussis.
- Interface: npm run check, npm test et npm run build réussis; 0 erreur, 11 warnings existants dans 5 fichiers.
- Installation: OpenCV 5.0.0.93 et NumPy 2.5.3 testés; lock du lanceur actualisé sans changer les versions précédentes.
- La valeur numérique structurée a également été exportée réellement, avec les mêmes pixels que l'overlay testé, en réutilisant la take 187. Aucun nouveau GPU pour cette vérification.

Les tests couvrent notamment le son natif non verbal, la piste réellement absente, les tours de parole manquants, la persistance/édition du manifest, les cues stables, l'archive avant suppression, le canon issu des plans exportés, la ponctuation numérique et un véritable export FFmpeg avec overlay. Les anciens tests qui validaient un fichier muet comme prise native utilisent désormais une prise avec son ou un mode silencieux explicite.

## Limites précises

- Le premier épisode a demandé des suppressions avant l'export. Ce blocage a été supprimé: une durée hors budget produit désormais une alerte visible, avec le film complet. La durée réelle peut dépasser 65 secondes; les dialogues ne sont jamais tronqués pour cacher ce dépassement. Une durée du MP4 incohérente avec les plans montés reste rejetée.
- Le test réel qualifie un échange simple de deux tours. Beaucoup de locuteurs, paroles simultanées et échanges longs ne sont pas garantis par FastH3 distillé.
- Le suivi du texte est 2D, avec translation, rotation et échelle. Occlusion, changement de plan interne ou forte perspective peuvent le faire échouer. L'export demande alors de corriger la zone de suivi ou de choisir un ancrage fixe; il ne rerend pas la vidéo.
- Les références historiques peuvent contenir des chiffres déjà dessinés. Pour une nouvelle série avec scores, prévoir des références et panneaux sans texte.
- Les lits procéduraux restent simples. Une musique importée peut fournir une identité plus riche; son ajout est facultatif, pas nécessaire à la présence d'une musique dans l'export.
- ASR/VAD vérifient les paroles, pas automatiquement le personnage qui les prononce ni le lip-sync exact. Les défauts mineurs restent acceptés.
- Les petites variations de visage, proportions, voix et cadrage propres au moteur subsistent.
- Aucun coût total facturé ni garantie de production autonome quotidienne n'est revendiqué.

## Prochain épisode réel

1. Choisir ou créer la vraie série, sa langue et ses personnages récurrents. Fixer le profil "fast_dialogue_social_fiction", l'identité musicale et un format numérique commun.
2. Définir les valeurs narratives initiales et leurs changements explicites dans la bible. Employer des références propres sans chiffres générés.
3. Donner une idée complète: hook immédiat, conflit, révélation ou renversement, cliffhanger. Demander des échanges et réactions dans le même plan quand la composition le permet, sans nombre fixe de plans.
4. Lancer un seul épisode depuis l'interface. Suivre les phases de rendu et conserver les prises valides; ne pas relancer tout l'épisode pour un défaut local.
5. Avant publication, regarder l'export original: dialogue complet, tours attribués, scores exacts, fond audible et voix lisibles, réactions utiles, durée réelle et fin dans la bonne langue.
6. Si nécessaire, corriger uniquement le plan ou l'ancrage concerné. L'export peut réutiliser les vidéos valides; ne pas refaire le GPU pour ajuster un score ou le mix.

StudioIA peut maintenant servir une vraie série à mise en scène simple avec revue avant publication. Le moteur, l'échange à deux et l'export ont une preuve réelle. Aucun blocage mécanique identifié sur ce run ne reste sans correctif. La production répétée de 65 secondes sans intervention n'est pas encore qualifiée: les écarts de durée après performance native et les plans à suivi difficile restent les risques concrets à surveiller.
