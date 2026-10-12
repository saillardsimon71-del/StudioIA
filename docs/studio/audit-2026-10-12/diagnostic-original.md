# Mort de honte - Diagnostic du rendu

12 octobre 2026. Série 18, épisode 1, projet 27, export 29. Analyse locale, sans modification du code, des prises ou du scénario enregistré dans StudioAI. Aucun nouveau rendu lancé.

## Ce qui est établi

L'export est terminé et dure 61,875 secondes. Les prises natives ont été conservées presque intégralement. Le retour utilisateur signale des passages audio incompréhensibles ou déplacés, une mauvaise première scène, un débit encore trop lent et une écriture insuffisante.

### L'image initiale contredit l'action centrale

L'image 383, utilisée comme entrée de la vidéo 385, montre Enzo assis dans un fauteuil du salon, un chariot sans lui et Jade debout. Le scénario et le prompt demandent Enzo assis dans le panier et Jade dans son fauteuil. Le premier plan vidéo commence donc sur une situation différente de l'histoire approuvée.

Le prompt d'image contient bien les positions souhaitées, mais ajoute ensuite la chorégraphie future : Clara se déplace derrière le chariot jusqu'à sa position finale. Ce passage provient de `blocking`, ajouté par `compile_image_request` dans `src/calliope/studio/visual_identity.py`. Il mélange la description d'un instant et le déroulement de la prise. Cela justifie de simplifier le prompt initial ; cela ne démontre pas que cette seule phrase a causé toute l'erreur d'image.

Les images du premier plan montrent ensuite Enzo hors champ et un rapprochement sur les deux femmes. Le plan suivant le reconstruit debout devant le chariot ; il redevient assis dedans plus tard. La continuation par image transmet ce qui a réellement été rendu, avec ses erreurs, et ne garantit pas l'état décrit dans le scénario.

### Le rythme laisse des attentes

Les timestamps de reconnaissance automatique montrent notamment des écarts d'environ 1,8 seconde entre deux répliques du premier plan et 2,7 secondes avant la dernière réplique du plan 216. Dans le plan 214, les dernières paroles reconnues finissent vers 11,2 secondes pour une prise de 15,1 secondes ; une partie de la fin est destinée au transfert du bouquet. Ces écarts ne sont pas tous des silences : ils peuvent contenir une action ou un son que la reconnaissance ne transcrit pas.

Le scénario initial contient 121 mots narratifs et 18 mots d'incitation. Étendre ce matériau à une minute avec des réactions successives et des gestes accessoires a laissé beaucoup de place à l'attente. La correction doit porter sur le jeu, les transitions et la progression dramatique, sans remplir la durée avec des pauses ni accélérer aveuglément tout le fichier.

### La reconnaissance ne certifie pas l'écoute

Les transcriptions enregistrées reconnaissent l'essentiel des phrases, mais rendent notamment "cagnotte" par "canyoute" et "commente GO" par "commande GO". Le premier cas est reproduit par une nouvelle transcription locale sans langue imposée. Ces résultats signalent une ambiguïté à écouter ; ils ne prouvent pas à eux seuls les mots réellement prononcés.

Les métadonnées indiquent `speaker_identity_status=unverified` : les noms de locuteurs des évaluations viennent du scénario, sans identification réelle des voix. Une mention "OK" ne prouve donc ni la bonne voix, ni la bonne bouche, ni l'intelligibilité perçue.

La nouvelle transcription automatique des plans 213 et 216 détecte le français et retrouve globalement les mêmes paroles. Elle n'isole pas tous les sons déplacés signalés par l'utilisateur. Les passages précis restent à confronter à son retour et à une écoute humaine.

## Ce qui ne constitue pas une cause démontrée

La syntaxe des champs, l'instruction de première image, les identifiants de locuteurs et les paroles dans `<d>[French] ...</d>` correspondent au [guide officiel H3](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/docs/VIDEO_PROMPT_WRITING_GUIDE_base_en.md), relu pour ce diagnostic. Aucune limite officielle à deux locuteurs n'a été trouvée dans ce guide. Une prise courte avec un échange plus simple est une expérience proposée, pas une restriction du modèle.

L'exécution utilise FastH3 V2 à huit étapes. Aucun test comparatif ne permet ici d'attribuer les défauts au nombre d'étapes, au moteur, à la longueur du prompt seule ou à un paramètre particulier.

## Révision proposée

Voir [le scénario et les prompts d'ouverture révisés](revision2-script.md).

La révision rend le faux décès explicite plus tôt, ajoute une tentative de fuite et une tentative de manipulation, puis fait envoyer la photo et appeler la mère avant la fin. Le bouquet et le linge sont retirés de l'ouverture proposée pour que le chariot et son occupant soient la seule action visuelle à comprendre.

L'essai utile serait une seule ouverture courte : vérifier d'abord une image montrant effectivement Enzo dans le chariot, puis regarder et écouter deux répliques dans cette composition. Si la prise fonctionne, étendre à la suite. Aucun nouveau contrôle automatique ni plafond créatif n'est ajouté par cette proposition.

## Preuves publiées

Voir [les limites et la liste des preuves](README.md), [la première image incorrecte](project-27-opening-wrong.png) et [les prompts et transcriptions du projet 27](project-27-snapshot.json). Les vidéos et le nouvel essai ASR restent locaux.
