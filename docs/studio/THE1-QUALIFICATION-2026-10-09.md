# Second run de qualification: The 1

## Preuves conservées avant modification

Base réelle: c768624ff8f72e8897cd189129ded643eb57adc5, branche codex/studioia-reliability. Série 9, projet 15, épisode 1, production 162. Production failed/video, progression 0,749; erreur au plan interne 117, plan humain 2.11. Aucun export de cet épisode. Aucun rendu supplémentaire ni reprise pendant cette qualification.

Le dossier externe de mission outputs/qualification-the1-15 contient before.db, evidence.json, asset-hashes.json, shots.csv, les prompts réellement envoyés par take, les probes des vidéos, les extractions WAV de diagnostic, canonical.jpg, source-images.jpg et video-frames.jpg. Les fichiers originaux restent à leur emplacement. Le snapshot contient 21 plans, 40 takes, 41 jobs, 13 entités et 70 fichiers. Le job enfant 202/take231 du plan118 est pending, rattaché au parent162 échoué: la queue autonome exclut ces enfants. Il n'a pas d'appel Modal.

shots.csv décrit chaque vidéo déjà générée: plan, image, références réellement envoyées, prompt H3, texte attendu, transcription, QC, durée brute, trim, approbation, CallID et container. evidence.json conserve aussi les images des plans encore non rendus, les tours attribués, jobs, timestamps et données audio. Les personnages réellement présents ont été examinés sur les planches de références, d'images et de frames; leur attribution complète à chaque frame n'est pas déduite automatiquement.

Il y a18 prises vidéo sur16 plans, jusqu'au2.11 inclus. Les durées generated du CSV sont celles du conteneur audio/vidéo (5,184 s); la piste vidéo des deux prises117 dure5,166667 s. Le champ approved du CSV est celui du plan au moment du snapshot, pas une approbation indépendante de chaque take: par exemple216 est l'essai rejeté et217 la prise ensuite choisie. Une absence de trim/QC ne doit pas être interprétée comme une preuve de qualité.

## Plan 117

Attendu: Student 2, `That's impossible, homie.` Une seule réplique dans le manifest; aucune voix Registry ni seconde réplique prévue. Référence source: image207. Les deux rendus bruts durent 5,166667 s pour 2 s utiles demandées. Aucun trim validé; dernière take230 sélectionnée mais non approuvée.

| Take | Appel Modal | Container | Inférence | Transcription conservée |
| --- | --- | --- | --- | --- |
| 229 | fc-01M4GTS0NMAJBS68V5C84CJ3JX | ta-01M4GT3R2RS5XEWHEK2ZP8TYZR | 31,913 s | That's impossible, homie. Leo doesn't rapon still dare to get no. Leo. |
| 230 | fc-01M4GTT8X6VRVJ9PW2RAQY3QFK | ta-01M4GT3R2RS5XEWHEK2ZP8TYZR | 31,845 s | That's impossible, homie. Leo doesn't Rapon's still at no. |

Le prompt exact est identique entre les tentatives. Il contient immédiatement après `</d>` la réaction `Leo doesn't respond - still staring at Noah` (tiret long dans le fichier original), puis `Leo remains frozen`. La correspondance des deux transcriptions avec cette directive est un indice fort de récitation. Les mots Leo/doesn't sont très confiants; les mots intermédiaires déformés empêchent d'affirmer une lecture verbatim parfaite. L'environnement d'analyse ne fournit pas d'entrée audio au modèle: aucune écoute directe n'est revendiquée. Les WAV restent disponibles pour vérification humaine.

Il ne s'agit pas d'une seconde réplique légitime du manifest, ni d'une voix Registry demandée, ni d'une simple réaction non lexicale. Une hallucination ASR reste possible, mais deux prises différentes reproduisant les mêmes mots hors dialogue rendent cette explication moins convaincante. Le QC extra est justifié par les données disponibles. La relance a gardé la même cause dans le même prompt. Modifier uniquement son seuil ferait disparaître l'alarme, pas le défaut.

Le guide officiel [MiniMax H3](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/docs/VIDEO_PROMPT_WRITING_GUIDE_base_en.md) autorise des réactions décrites autour des répliques. Déplacer la direction visuelle avant le bloc de paroles est une mitigation ciblée du comportement observé de FastH3, pas une nouvelle obligation officielle ni une garantie du moteur.

Take229: queued17:18:33.750, submitted17:18:36.275, function entry17:18:36.302, loading17:18:36.339, generating17:18:40.436, artifact17:19:08.251, downloading17:19:11.927, validation17:19:12.404, échec17:19:15.007 UTC. Take230: queued17:19:15.028, submitted17:19:17.465, entry17:19:17.521, loading17:19:17.557, generating17:19:21.657, downloading17:19:54.977, validation17:19:55.449, échec17:19:57.106 UTC. Pas de preuve de panne Modal.

## Identité visuelle

Le défaut commence avant H3. Les canons noah_teen, student_1 et student_2 sont humains; leo_baby est un bébé humain. Elise et Teacher ont une tête de fruit sans visage lisible dans leurs références. Les descriptions ne fixent aucune espèce ni forme précise. Les variantes bébé/adolescent sont générées indépendamment, sans référence parent.

Les fichiers de référence sont réellement transmis au service image, mais comme liste non nommée, limitée aux quatre premiers. Les prompts d'image restent ceux du plan, sans compilation systématique des identités et du style canonique. Exemple 1.1: quatre personnages remplissent la limite et le lieu est omis. En 2.3/2.4, Noah est humain; en 2.8 Leo devient humain également. En 3.2/3.3, Maximilian devient humain malgré son canon fruit. Certaines images demandent deux cadrages ou un cut dans un seul still.

Le run n'a aucune validation visuelle persistée des canons, aucun lien de variante explicite et aucune association index de référence/personnage dans le prompt envoyé. Ajouter seulement des mots au prompt n'apporterait pas une preuve de conformité d'un canon déjà incorrect.

## Compteurs

Toutes les listes screen_text du manifest narratif sont vides; display_values est vide. Le profil avait pourtant number_format=plain et la bible demandait des overlays. Le plan2.10 décrit le changement de Noah en prose sans valeur ni temps structurés. Aucun tracking et aucun overlay déterministe n'a donc été appliqué à cet épisode. Les chiffres/lettres visibles dans les vidéos brutes sont génératifs, par exemple le texte mutant au-dessus du père en1.1 et le grand1 sur le front en2.10. Ce n'est pas une inversion mesurée du tracker sur cet export: il n'y a pas d'export.

Le tracker existant utilisait une ROI issue du manifest, des points optiques et un champ entity_key nominal. Il ne vérifiait ni l'identité assignée à la ROI ni la source réellement suivie. Cette faiblesse doit être corrigée avant de prétendre attacher deux scores sans ambiguïté.

## Autres constats

Take216, plan1.4, a été rejetée pour nombre modifié alors que la transcription fiable disait500 et le dialogue attendu five hundred. La comparaison mot par mot ne normalisait pas les nombres composés. Take217 a consommé une relance supplémentaire; son transcript est disponible, mais son QC disait unknown, "Mots divergents insuffisamment certains". Le replay local corrigé accepte maintenant les deux prises pour cette comparaison, sans les modifier ni les réapprouver dans le projet.

Plan2.7/take225: préfixe transcrit `Art of Pings off a dotting your right needs.` suivi de la réplique complète. QC unknown car plusieurs mots divergents ont une confiance insuffisante. Ces données ne permettent pas de trancher entre parole réellement mauvaise et hallucination ASR. La fenêtre complète5,042 s a été conservée. Ne pas présenter unknown comme validation sonore positive.

Le mix prévu existe: musique tension, ambience city, ducking, beep/impact/riser et holds. Il n'a pas été rendu puisque l'export n'a pas démarré. Deux cues utilisent shot_index21 avec shot_id null, hors des21 plans indexés0..20. Leur synchronisation n'est donc pas qualifiée. Aucun stem final ni mix final de cet épisode n'existe.

Les prompts canoniques effectivement envoyés lors de ce run n'ont pas été persistés. Les requêtes d'image/H3 des takes, les références utilisées, les transcriptions et les phases existent. Facture GPU, temps facturé, diarisation et vérification perceptuelle automatisée n'existent pas: ne pas les reconstruire depuis des estimations.

## Correctifs et qualification

### Prompt et parole

- Le compilateur place la direction visuelle, le canon et les réactions avant le bloc des paroles. Aucun texte de réaction n'est ajouté après les balises de dialogue. Plusieurs tours successifs restent possibles avec attribution et speaker IDs explicites; le texte parlé exact est conservé.
- Le QC conserve le rejet des paroles inventées. Une correspondance suffisamment fiable avec la prose du prompt produit un diagnostic de récitation, persisté avec le QC, pour expliquer une relance sans masquer le défaut.
- Les nombres anglais composés et leur transcription chiffrée sont comparés comme la même valeur. Un autre nombre reste bloquant. Sur les transcriptions historiques: 216 et217 deviennent ok; 229 et230 restent extra, diagnostic instruction_recitation; les incertitudes214/219/225 restent unknown.

### Canon et sources

- Les entités possèdent une identité visuelle explicite, une variante de référence facultative et une approbation liée au hash du fichier. Un canon incorrect ne reçoit pas d'approbation automatique. Les variantes réutilisent leur base, et les modifications d'identité/style invalident les approbations concernées.
- Le compilateur image associe chaque index de référence à une identité précise. Il conserve tous les personnages et le lieu requis, sans troncature aux quatre premiers fichiers. Le prompt réellement envoyé et les hashes des références sont persistés.
- Les valeurs et leurs changements sont des données structurées. Le compilateur retire les demandes de typographie générative des parties visuelles, réserve une zone libre, et conserve le fait narratif ainsi que les nombres prononcés. Aucun filtre textuel ne garantit à lui seul l'absence de chiffres halluciné dans les pixels: les images réelles sont donc contrôlées avant la dépense vidéo.
- Le profil permet d'exiger une vérification des références visuelles, des images de départ et des vidéos obtenues. Ces approbations sont explicites, rattachées au fichier et au contrat visuel. Une modification de source ou de contrat oblige à les renouveler. Le studio peut préparer manuellement un nouveau contrat d'un épisode interrompu sans lancer de rendu et sans effacer les anciennes prises.
- La continuation vidéo utilise la vraie frame retenue du plan précédent. Cette frame doit être approuvée pour les identités et zones du plan cible; son hash est revérifié sur l'image réellement préparée avant soumission. Aucun remplacement automatique de la continuation par un still arbitraire.

### Compteurs

- Une valeur canonique configurée exige un label numérique associé à l'entité visible, même si le bouton du profil a ensuite été désactivé. Une omission ne peut plus disparaître silencieusement comme dans le manifest historique.
- La continuité du nombre est contrôlée dans l'ordre narratif. Le changement dans un plan utilise `changes: [{"at_sec": 1.2, "value": 2}]`; les secondes sont celles de la portion utile montée. Le plan suivant démarre sur cette valeur finale. Aucun nombre n'est extrait automatiquement de la prose.
- L'association personnage/ROI doit être approuvée sur le vrai fichier, à une frame précise. L'approbation d'une image ne suffit pas à autoriser le tracking d'une vidéo. L'overlay est lié au hash de la source, à la take et à la zone approuvée.
- Le suivi combine points optiques, retour aller/retour et vérification du patch. Une zone ambiguë, une occultation ou un croisement provoque un refus explicite, sans permutation ni position silencieuse de secours. Seule la portion retenue au montage doit être suivie avec succès. Ce mécanisme ne reconnaît pas sémantiquement un personnage et ne qualifie pas tous les mouvements possibles.

### Preuves visuelles locales

Le script `calliope-backend/scripts/qualify_fruit_overlays.py` construit des fixtures dans un dossier de sortie séparé, à partir des références fruit existantes. Aucun fichier du run n'est remplacé. Le dossier externe outputs/qualification-fruit-overlays contient le MP4 fruit-overlay-proof.mp4, fruit-overlay-contact.png et qualification.json.

| Cas | Résultat observé | Limite |
| --- | --- | --- |
| A: deux fruits | 1 et500, valeurs exactes et attribuées séparément, sans unité ni ponctuation | Maximilian sert de substitut fruit au canon Noah incorrect |
| B: petits déplacements | Pas d'inversion; erreur maximale de suivi0,064 et0,544 pixel sur720x1280 | Calques rigides découpés, pas un nouveau rendu H3 |
| C: transition | Frame23:1, frame24:2 à1 s/24fps; 500 inchangé | Temps imposé par la fixture, pas déduit de la parole |
| D: compilation | Les demandes de nombres sur le front/badge sont supprimées, le dialogue et les identités sont préservés | La conformité des pixels générés demande une revue réelle |
| E: continuité | Tests des associations de références, variantes et hashes; changement de canon/source invalide l'approbation | Pas de nouvelle qualification générative de Noah fruit |

### Protocole minimal de reprise

1. Garder le projet15 interrompu et ses prises archivées. Dans la série9, activer "Vérifier les références visuelles" et les valeurs attachées, format plain. Définir les identités fruit immuables et les liens de variantes sans inventer les valeurs manquantes. Donner à Noah/Leo leurs valeurs1/500 et des clés de labels stables entre variantes.
2. Importer ou préparer uniquement les canons/images incorrects, puis approuver visuellement les références. Ne pas approuver un canon humain. Le contrat visuel doit être propre avant tout nouvel appel vidéo.
3. Dans les plans conservés, renseigner les labels associés aux entités et les zones réelles; placer explicitement le changement1 vers2 au beat souhaité. Corriger aussi les deux cues audio hors plage. Utiliser "Préparer le contrat visuel depuis la série" pour actualiser l'épisode interrompu; cette action conserve les prises et laisse la production en pause.
4. Confirmer l'identité et les zones sur chaque image de départ correcte. Conserver les clips déjà réellement utilisables; ne pas attendre d'un nouveau canon qu'il corrige rétrospectivement leurs pixels.
5. Avant un nouveau lancement complet, qualifier au maximum un court plan ciblé du type117 avec le prompt corrigé et des références fruit approuvées. Vérifier la réplique exacte, l'absence de récitation, les tours successifs si présents et l'identité. Aucun de ces appels n'a été lancé pendant cette mission.
6. Confirmer les ancrages sur la vidéo réelle, à la frame choisie, puis tester le montage local avec les clips déjà valides. Les anciennes prises229/230 restent rejetées; ne pas augmenter leur budget de relance pour les faire passer.

### Verdict et limites ouvertes

Le logiciel corrige les causes identifiées et fournit les contrôles nécessaires pour une reprise sélective. Cliquer immédiatement sur "Reprendre" dans le projet15 reste inapproprié: son canon historique contient des humains, son manifest n'a aucun label structuré et le nouveau comportement H3 n'a pas encore reçu de preuve générative. Une correction locale des fixtures ou une suite verte ne prouve pas le contraire.

Les approbations visuelles restent humaines. Le tracker préfère refuser une occultation ou un croisement à échanger les valeurs; la génération autonome à grande échelle avec ces mouvements n'est pas qualifiée. Les transcripts incertains ne sont pas une preuve d'intelligibilité. Le mix final de cet épisode, les deux cues hors plage et les réactions de H3 au prompt corrigé restent à qualifier. Aucun nouvel épisode, aucun GPU et aucune reprise du run actuel n'ont été lancés.

### Validation finale

- Suite backend complète avec `pytest --asyncio-mode=auto -q`: **1100 passed, 2 skipped, 2 warnings, 0 failed**, en448,08 s. Les deux warnings concernent le nettoyage asyncio/Proactor Windows connu. Log conservé dans outputs/qualification-the1-15/full-tests.log.
- Vérifications ciblées: 50 tests identité/intégration (dont continuation vérifiée), 29 tests manifest/overlay, puis11 tests manifest après le contrôle du canon de l'outro. Ces groupes se recouvrent; leurs nombres ne s'additionnent pas à celui de la suite complète.
- Interface: tests npm réussis, vérification Svelte0 erreur/11 warnings préexistants, build de production réussi. Ruff sur tous les fichiers Python modifiés et git diff --check réussis.
- Replay des transcriptions historiques et fixtures visuelles locaux exécutés, sans provider ni GPU. Les preuves visuelles ont été ouvertes et examinées.
- Comparaison des colonnes d'origine du snapshot: tous les enregistrements concernés des projets11/15 et de la série9 sont identiques; les70 assets sont inchangés. Le MP4 original du projet11 conserve le SHA256 `8db8eb1505f5644f3db22e1a730947cb6a7b283d2e3ef95d21bbcc828f131afd`. Résultat conservé dans preservation.json. Le seul job encore pending est202, enfant du parent162 échoué, sans déclenchement autonome.

Ces résultats valident le logiciel et les cas locaux. Ils ne constituent pas un nouveau rendu H3 réussi ni un épisode final qualifié.

Activation: l'interface a été reconstruite. Le redémarrage automatique du serveur local a été refusé par le contrôle d'approbation ("blocked by policy", sans motif détaillé). Le serveur actif ne doit donc pas être présenté comme exécutant déjà le nouveau code. Après livraison, arrêter ce serveur puis relancer launch.cmd; cette relance applique la migration de schéma, sans reprendre automatiquement le parent162 échoué.
