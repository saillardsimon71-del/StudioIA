# Observation du parcours idee -> film de 15 secondes

## Perimetre et autorisations

Demande : observer le travail du Studio, conserver les preuves, corriger ses mecanismes, puis verifier sur un film reel. Sujet choisi par l'utilisateur : compteur de bodycount, hook et intrigue. L'utilisateur autorise les appels texte DeepSeek. Il n'a pas autorise de GPU, TTS ou generation d'images pour ce test.

Serie isolee 33, projet 47, job 680. Requete initiale conservee dans `work/bodycount15-proof/episode-request.json`. Deux personnages humains adultes, un lieu, 15 secondes, aucune reprise du scenario En prison. DeepSeek invente les personnages et toutes les paroles. Aucun scenario corrige manuellement n'est injecte.

Le serveur principal est sur 8247. Son redemarrage a ete refuse par le controle automatique. La validation des corrections utilise donc le meme code sur 8252, avec une copie coherente de la base dans `work/bodycount15-proof/data/calliope.db`. Les modifications dans cette copie ne sont pas des modifications des episodes de la base principale.

## Premier passage, code initial

Les diagnostics originaux sont dans `calliope-backend/data/assets/47/studio/diagnostics/`.

1. Le Studio demande plusieurs pistes, puis une scene ecrite et selectionnee par DeepSeek.
2. Le texte "Douze, pas onze" annonce un ancien chiffre de sept, puis invoque onze sans en etablir le calcul. Le compteur de partenaires passes monte pendant une conversation sans nouveau partenaire etabli. La selection editoriale par l'auteur ne detecte pas ces contradictions.
3. Le decoupage technique tente six prises. La preparation des etats et les corrections se poursuivent alors que la logique du recit est deja defectueuse.
4. La validation du compteur refuse ses changements : `Un changement de valeur necessite une valeur initiale et un evenement.` La premiere reparation introduit `initial_value`, champ absent du modele `ScreenText`. La deuxieme revient a une combinaison refusee. Les trois diagnostics `planner-680-27c4e47e244141659df14afa1023c384-attempt-1.json` a `attempt-3.json` conservent ces refus.
5. Le travail finit en echec avant tout media. Cet echec ne constitue pas un storyboard valide.

Causes dans le code : `_develop_creative_story` passait directement de la selection a la realisation, sans revue separee du recit. Le reparateur recevait un diagnostic et des chemins de champs, mais pas le schema exact de `ScreenText`.

## Premier passage avec revue narrative

Preuves : `work/bodycount15-proof/inspection-run1.json`, `isolated-run1-err.log` et les diagnostics sous `data/assets/47/studio/diagnostics/`.

1. DeepSeek propose 104 mots pour 15 secondes. Le validateur refuse, puis refuse 59 mots ; 38 mots sont acceptes a la troisieme tentative.
2. La nouvelle revue trouve la confusion entre nombre de partenaires et nombre de mensonges. Elle refuse le recit avant le decoupage technique.
3. Le Studio demande une reecriture a l'auteur. Ses 60 mots sont refuses, puis 46 sont acceptes par l'ancien seuil tolerant deux secondes supplementaires.
4. La deuxieme revue retrouve la meme incoherence de regle. Le pipeline s'arrete comme prevu apres une reecriture, sans lancer le decoupage, les images ou le GPU.
5. La revue LLM donne aussi une justification de debit mathematiquement fausse. Son comptage ne doit pas remplacer les controles logiciels.

Ce passage prouve le blocage avant production. Il ne prouve pas la capacite a produire un bon scenario.

## Corrections generales et verifications

- Revue narrative par un appel separe avant realisation, avec rapport conserve. Une seule reecriture puis une seconde revue ; un refus ou rapport mal forme bloque. Les scenarios fournis explicitement et les adaptations verrouillees gardent leur parcours existant.
- Budget de mots structure transmis au premier appel d'ecriture. Le seuil de parole ne peut plus emprunter deux secondes a la duree demandee. Le nombre recommande laisse une marge aux gestes ; ce budget n'est pas une mesure de diction du film genere.
- Le logiciel compte les mots et transmet ce comptage au critique. Celui-ci recoit la consigne de juger le recit, sans inventer de calcul de debit.
- Une revision peut abandonner une premisse incoherente au lieu de raccourcir le meme mauvais texte. Les nombres gardent leur signification et necessitent une cause pour changer.
- Le schema reel de `ScreenText` est fourni au planificateur et au reparateur, avec exemple de valeur initiale `value`, evenement `change` et changements chronologiques. La validation n'est pas assouplie et aucune valeur n'est devinee par le logiciel.

Tests nouveaux vus en echec avant implementation, puis passes. Lot ecriture/revue/reparation/compteurs : 113 passes. Lot complementaire : 78 passes. Apres ajout des controles de montage : 252 tests cibles passes. Dernier lot incluant le controle de compilation LTX : **262 passes en 94,36 s**, sortie 0, conserve dans `work/bodycount15-proof/regressions-final-verified.log`.

Les tests frontend `npm test` passent egalement. Aucun changement frontend n'a ete fait dans cette passe.

## Deuxieme passage et faux positif de la revue

Le budget refuse 47 mots, puis accepte une scene plus courte. La revue approuve "Le compteur de la nuit derniere". La lecture des actions montre pourtant une contradiction : le compteur de Marion vaut 3 a l'ouverture, puis devient 4 pendant la scene alors que le dialogue presente le changement comme passe. Cette approbation est un faux positif de revue LLM, pas une preuve de coherence.

La realisation brute de DeepSeek prevoit six prises de 15 secondes au total, chacune contenant une replique. Les cinq suivantes sont declarees continuations techniques. Dans ce cas precis, le compilateur LTX n'a pas cree cette fragmentation : elle est deja presente dans la reponse brute du planificateur.

L'export existant compte ces six segments et les refuse : 5 coupes pour 15 secondes, maximum 2. Le Director ne reprenait pas ce controle avant l'approbation/GPU. Deux corrections generales ont ete ajoutees :

- Le planificateur recoit le budget de coupes et demande automatiquement a DeepSeek de regrouper les prises si son resultat le depasse. Les paroles, locuteurs et leur ordre restent verrouilles.
- Le meme garde-fou est execute lors de l'approbation et du preflight Director, avec l'exception existante des videos sources dont la cadence est approuvee et validee separement.

Verification reelle sur le storyboard refuse : `preflight-refused-run2.json` contient `Montage non conforme avant GPU : Trop de coupes : 5 pour 15.0 s (maximum 2).` Aucun GPU n'a ete lance.

## Dernier passage reel, job 681

Le redemarrage standard de l'essai a archive l'ancien storyboard dans la base de test. Les paroles n'ont pas ete ecrites ou corrigees par l'operateur.

- Titre DeepSeek : "Le compteur qui repond".
- 15 secondes : prises 429 (6,5 s), 430 (5 s), 431 (3,5 s).
- Six repliques, deux locuteurs dans chacune des trois prises.
- Casting, positions, regards, cadrage, camera, son, etats, chronologie, temps de parole et criteres QC remplis par DeepSeek.
- Ambiance configuree : `room`, -34 dB ; musique `none`. Ce sont des parametres, pas une ecoute validee.
- Le recit assume cette fois une anomalie observee par les personnages : leurs compteurs bougent tout seuls. La revue a refuse une premiere version puis approuve la reecriture. Son approbation ne garantit pas la qualite subjective de ce cliffhanger.

Le preflight reste refuse, et aucun paquet d'images n'a ete approuve :

1. Prompts LTX : 207 et 184 mots apres reduction, plafond du compilateur 180. Les contraintes de parole ne sont pas supprimees pour forcer le passage.
2. Premiere frame 431 : bouilloire allumee et vapeur deja visibles, mais `initial_state.bouilloire` la donne eteinte. Le JSON d'etat et la description libre d'image sont deux sources divergentes.
3. Chronologie 429 : la decouverte du compteur par Marion est decrite avant la replique "Regarde le tien", puis repetee ensuite. Le planificateur decrit un meme evenement dans plusieurs champs ; `action_sequence` les agrege comme des textes distincts. La conservation litterale de chaque texte ne garantit pas l'unicite de l'evenement.
4. Les compteurs sont mis en scene sur des poignets mobiles alors que les surimpressions sont fixes a l'ecran. Le placement visuel reste a verifier ; il ne peut pas etre qualifie de suivi de poignet.

La compilation du prompt est maintenant verifiee pendant la planification Director/LTX, en simulant le meme regroupement des actions que le Director. Un depassement demande une correction des descriptions de mouvement, sans toucher aux paroles, aux locuteurs ou a leur ordre. Un test simule verifie cette reparation. Le replay de la vraie reponse du job 681 et de ses trois reponses d'etats confirme que le nouveau controle intercepte exactement le depassement de 207 mots AVANT enregistrement du storyboard : `recorded-prompt-replay.json`. Ce replay n'a effectue aucun appel texte ou GPU. Il prouve la detection precoce, pas la reussite d'une nouvelle correction reelle par DeepSeek.

Les incoherences d'image initiale, les doublons semantiques de gestes et le suivi des compteurs restent ouverts. Une nouvelle passe payante n'a pas ete lancee pour les masquer. Le storyboard reste non approuve.

## Preservation et verdict

La serie 30 est identique au snapshot de depart. Les empreintes des medias sont inchangees :

- Episode 1 publie : `7ab17711fc0a4aa49842108c6f2bbfe5aa81b791077085f615371234760efda3`.
- Golden episode 2 V4 : `1465e4cecc8a72887e907c8f37e4c73e8c8ee7f8ef13c8e497c46d67979d7cef`.

Seuls les appels texte autorises ont ete utilises. Aucune image, prise GPU, synthese TTS ou livraison MP4 nouvelle n'a ete generee. Aucun changement Octopus.

Verdict : le test de production de 15 secondes n'est pas valide. Les controles detectent davantage de defauts en amont, mais un parcours fiable de l'idee au film n'est pas encore demontre. Les prochains changements doivent traiter la source unique de verite des etats/images et des evenements, avant de demander des images et de payer un rendu.

## Critere de fiabilite restant

Un scenario coherent puis un storyboard complet et valide ne suffisent pas a declarer le parcours fiable. Il reste a obtenir les images manuelles, verifier le preflight et le budget, produire les prises, controler toutes les paroles et images, puis exporter et regarder les 15 secondes entieres. Les erreurs de voix, de jeu, de synchronisation et de raccord ne sont pas couvertes par une simple revue texte.

Chaque nouvelle tentative et intervention doit etre notee. Aucun echec ne doit etre transforme en succes par une correction manuelle du scenario, un montage externe ou une approbation de QC non verifie.
