# Dossier pour audit indépendant - 12 octobre 2026

Cette branche publie la version locale de StudioAI et les preuves utiles pour auditer son parcours de production. Elle ne constitue pas une certification de qualité audiovisuelle. Les corrections existantes sont conservées pour examiner leurs effets, y compris leur complexité.

## Sources à lire

Le code est dans `calliope-backend/src/calliope/studio/`, les routeurs dans `calliope-backend/src/calliope/routers/`, l'adaptateur H3 dans `calliope-backend/src/calliope/engines/` et son déploiement dans `deploy/`. L'interface est dans `calliope-web/src/lib/studio/` et `calliope-web/src/routes/studio/`.

Le [prompt d'audit](PROMPT_AUDIT.md) définit la mission. Lire [les réglages publics de l'essai](runtime-settings-public.json) et [la vérification de publication](verification.md).

## Cas 27 : premier rendu

- [Diagnostic du rendu](diagnostic-original.md).
- [Prompts enregistrés, répliques, prises et diagnostics automatiques](project-27-snapshot.json).
- [Première image utilisée](project-27-opening-wrong.png) : Enzo est dans un fauteuil, alors que le scénario le demande dans le panier du chariot.

Les problèmes d'écoute et de qualité narrative ont été signalés par l'utilisateur. Les transcriptions sont des observations automatiques ; elles ne prouvent ni l'intelligibilité humaine ni l'identité des voix. Les vidéos et la transcription indépendante restent locales.

## Cas 28 : version retravaillée et contournement

- [Scénario approuvé et compte rendu d'exécution](revision2-script.md).
- [Brief envoyé à StudioAI](revision2-brief.json).
- [Aperçu avant préparation séparée des images](opening-preview-before-manual-composition.json) : ce n'est pas la demande finalement utilisée pour l'image importée.
- [Image 390 rejetée](project-28-opening-inverted.png) : positions inversées.
- [Image 391 rejetée](project-28-opening-duplicated.png) : personnage supplémentaire et mauvaise composition.
- [Première composition séparée rejetée](project-28-manual-opening-rejected.png) : personnage supplémentaire.
- [Image finalement retenue](project-28-opening-accepted.png) et [sa demande](opening-request-accepted.json).
- [Image d'incitation retenue](project-28-midroll-accepted.png) et [sa demande](midroll-request-accepted.json).
- [Snapshot du projet 28](project-28-snapshot.json) : prises, prompts vidéo enregistrés et état à la capture.

La composition retenue a été préparée en dehors du compilateur habituel avec le même service d'images Agnes, puis importée comme prise 393. L'image d'incitation a été créée à partir de cette composition et importée comme prise 394. Le degré d'humanisation des visages féminins diffère des portraits initiaux. La vérification visuelle confirme les positions et l'absence de personnage supplémentaire ; elle ne démontre pas une identité parfaite ni un film réussi.

Les [scripts ponctuels du lancement](manual-helpers/) illustrent le travail manuel à éliminer. Leurs chemins locaux sont remplacés par `AUDIT_WORKSPACE` et `REPOSITORY` : ce sont des pièces documentaires, pas des outils à exécuter. Le clonage des références et la préparation séparée des images sont extérieurs au parcours normal de l'interface.

## Limites

- Captures datées, sans suivi en temps réel. Le projet 28 est terminé techniquement à la capture ; aucune nouvelle validation humaine de son audio ou de son intérêt narratif n'est revendiquée.
- `LOCAL_ASSET/` indique un média local, pas une URL téléchargeable. Les vidéos, la base SQLite, les clés, les comptes et la configuration complète ne sont pas publiés.
- Seules les images nécessaires au cas sont copiées ici. Les portraits de référence et les logs fournisseur complets restent locaux.
- Les snapshots sélectionnent les champs utiles à l'audit. Distinguer aperçu, prompt envoyé, média importé et média généré.
- Les tests valident des comportements logiciels ; ils ne mesurent pas la rétention ni la qualité créative.

Séparer faits, hypothèses causales et informations manquantes. Ne pas relancer les helpers ni soumettre de génération payante.
