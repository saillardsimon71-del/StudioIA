# Studio Director - parcours manuel ChatGPT

## Utilisation

1. Ouvrir une serie et choisir "Creer un nouvel episode". Saisir l'idee, puis eventuellement la duree et le ton. DeepSeek prepare le recit et les contrats de realisation. Aucun media n'est genere a cette etape.
2. Relire le storyboard. Pour une revision, selectionner les plans et decrire le changement. Les autres contrats et medias restent conserves. Un changement d'etat incompatible avec un raccord exige de selectionner egalement le plan dependant concerne.
3. Valider le storyboard. Le ZIP "paquet pour ChatGPT" contient `manifest.json`, un prompt par premiere frame et les images de reference disponibles. Les references absentes sont signalees dans le manifest. Les zones de texte sont laissees vierges pour le montage.
4. Dans la conversation ChatGPT, joindre le prompt et ses references et generer l'image manuellement. Conserver exactement le nom du manifest. Studio IA n'envoie aucune demande a ChatGPT et n'appelle aucun fournisseur d'images.
5. Importer les PNG/JPEG/WebP par lot et verifier chaque image. Les noms associent les images aux plans et a leur revision. Un nom inconnu, un doublon dans le lot ou un fichier illisible fait rejeter le lot avant insertion en base.
6. Dans le panneau expert du budget, renseigner une estimation en EUR par seconde video generee. Ce chiffre est une hypothese operateur, pas un tarif fournisseur. Regler les plafonds de prises, secondes et cout estime, puis lancer le preflight local.
7. Produire la prochaine prise. Le GPU est libere avant l'attente de votre QC. Ecouter et regarder le segment conserve: paroles exactes, bons locuteurs, identites, cadrage, texte et raccords. Le bouton "Verifier cette prise" utilise la transcription locale lorsqu'elle est disponible. Sans transcription, l'audio reste "non verifie"; la validation humaine est explicite.
8. Valider la prise et continuer. Pour un raccord, le parcours montre la derniere frame precedente a confirmer. Pour une mauvaise prise, rejeter puis choisir "Refaire ce plan et ses seuls raccords dependants". Les prises anterieures restent accessibles. Annuler un travail depuis le parcours ou mettre la production en pause.
9. Lorsque les prises sont validees, continuer le montage et telecharger le MP4. L'incitation marketing CTA existante reste presente.

Les anciens episodes et leurs medias restent accessibles. "Preparer les contrats du Director" active ce parcours sur un episode deja decoupe. Le panneau expert historique conserve les reglages manuels, les prises et le montage. Les anciennes demandes de generation reelle d'images sont bloquees par l'adaptateur; ses tests historiques restent executes uniquement en simulation avec transports simules.

## Contrat et memoire

`director_contracts.py` definit le role et le contrat strict: resume operateur, casting, lieu, premiere frame, positions, regards, cadrage, camera, action, etats initiaux/finaux, chronologie, repliques exactes avec locuteur et fenetre temporelle, son, duree, references, raccords et criteres QC. Les champs inconnus, valeurs non finies, locuteurs inconnus, temps hors duree et raccords incompatibles sont refuses.

La bible, les identites, les references et les voix du canon natif restent dans les tables existantes. L'historique canonique inclut les evenements et les etats de chaque plan des episodes exportes et engages dans la continuite. Les nouvelles tables ajoutent les contrats, l'approbation du storyboard et les reservations de cout. Les archives de redemarrage incluent ces donnees; le compteur des demandes reservees reste conserve.

Le Director n'a que la preparation et la proposition de revisions sur des IDs explicites. Il ne recoit aucun outil shell, image, GPU, suppression ou publication. La cle API locale sert uniquement a l'authentification du client texte existant. Elle n'est pas inseree dans les prompts, le paquet ou les fichiers suivis.

Les choix H3/LTX passent par leurs adaptateurs locaux. Le preflight utilise les durees reelles arrondies aux frames et valide localement la requete. Les temps du contrat sont compiles en instructions de realisation, sans inventer de parametres moteur.

## Garde-fous et limites du MVP

- Approbations liees au contenu: storyboard, premiere image, fichier video, contrat et points de coupe. Modifier une source invalide les controles concernes.
- Validation et reservations avant GPU, y compris les demandes secondaires et la reprise d'un travail. Changer le budget recontrole les demandes non soumises. Les reservations sont conservatrices: une demande annulee peut rester comptabilisee; aucun remboursement fournisseur n'est presume.
- Une prise Director n'est jamais approuvee automatiquement. Un defaut de parole detecte bloque l'approbation. L'identite du locuteur et les controles visuels restent humains. La transcription d'une prise complete ne garantit pas une coupe manuelle: verifier le segment conserve indique dans l'interface.
- Aucun modele de vision ou de reconnaissance de locuteur n'est ajoute. Aucun prix ou plafond de facture Modal n'est garanti par l'estimation locale.
- Ce MVP revise les plans existants. L'ajout/suppression conversationnelle de plans reste a faire; pour changer leur nombre, le redemarrage explicite archive les prises avant une nouvelle preparation. Une revision narrative actualise les evenements et etats de l'episode; l'ancien texte d'intrigue ouverte est retire pour ne pas transmettre un fait devenu faux.
- Les tests utilisent des services simules et des medias synthetiques locaux. Aucun rendu GPU, appel d'image ou appel DeepSeek reel n'a ete necessaire a la validation de cette evolution.

## Relance Windows

Arreter l'ancienne fenetre du Studio avec Ctrl+C apres les operations en cours, puis dans PowerShell:

```powershell
Set-Location 'C:\Users\saill\Documents\Codex\2026-10-07\files-pasted-by-the-user-mission\outputs\studio-ia'
.\launch.cmd -Rebuild
```

Ouvrir `http://127.0.0.1:8247/studio`. La compilation de verification a utilise `STUDIO_BUILD_DIR` dans `work/`, pour conserver l'interface servie par l'ancien processus jusqu'a sa relance.

## Tests sans depense

```powershell
Set-Location 'C:\Users\saill\Documents\Codex\2026-10-07\files-pasted-by-the-user-mission\outputs\studio-ia'
.\.venv\Scripts\python.exe -m pytest calliope-backend/tests/test_studio_director.py -q
Set-Location calliope-web
npm.cmd test
npm.cmd run check
```
