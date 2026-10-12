import asyncio
import json
from pathlib import Path

import httpx

from calliope.studio import series, service
from calliope.config import settings

root = Path(r'AUDIT_WORKSPACE')
state_path = root / 'work/mort-de-honte-revision2-launch.json'
state = json.loads(state_path.read_text(encoding='utf-8-sig'))
with service.database() as conn:
    entities = {entity['key']: entity for entity in series.entities(conn, state['series_id'])}
request = {
    'image_base_url': settings.studio_image_base_url,
    'image_model': settings.studio_image_model,
    'aspect_ratio': '9:16',
    'reference_paths': [entities[key]['reference_path'] for key in ('enzo', 'clara', 'jade')],
    'prompt': "Create ONE new vertical 9:16 animated film frame containing exactly THREE fruit-headed adult characters. The supplied portraits identify their faces and clothes; build a completely new shared composition. Reference 1 identifies ENZO, the yellow banana-headed man in a white jacket and dark trousers. Reference 2 identifies CLARA, the red strawberry-headed woman in a cream jacket and black dress. Reference 3 identifies JADE, the blue-purple blueberry-headed woman with black bob and lilac jacket.\n\nIn the CENTER FOREGROUND is a large METAL SUPERMARKET SHOPPING CART. ENZO is the cart's sole passenger: he is sitting down INSIDE its deep wire basket, with his hips and folded legs contained by the wire sides. His shoulders and banana face are above the basket rim. The cart's four wheels are visible on the floor. CLARA stands on the LEFT BEHIND THE CART HANDLE, holding it with both hands, facing Jade. JADE stands on the RIGHT ON THE FLOOR beside the cart, facing Clara. The two women are upright and outside the basket.\n\nThe frame's three heads are one yellow banana in the basket, one red strawberry behind the handle and one blue-purple blueberry beside the cart. They exchange tense looks. One cart, one seated man, two standing women. No other people or duplicate heads. All salon chairs belong to the distant background, away from the characters and cart.\n\nEye-level three-quarter medium-wide shared view, adults fill the lower two thirds while all faces, hands, basket and wheels remain readable. Rich warm hair salon with glowing vanity mirrors, shelves of products, plants, marble counters and a glass entry door behind them. Detailed stylized 3D materials, warm side lighting, expressive faces and human bodies, matching the three supplied portraits. A single frozen initial composition, no caption, collage or later event."
}
artifact = root / 'work/mort-de-honte-revision2-opening-manual.png'
record_path = root / 'work/mort-de-honte-revision2-opening-request.json'
request['reference_paths'] = [entities['enzo']['reference_path']]
request['prompt'] = "A single vertical 9:16 frame from a detailed 3D animated comedy. Exactly THREE adults and three heads in the frame. CENTER: the banana-headed young adult man in the supplied portrait, wearing his white jacket, dark top, dark trousers and sneakers, seated INSIDE the deep metal wire basket of a supermarket shopping cart, knees folded within the basket, torso and yellow banana head above its rim. LEFT: Clara, an elegant young adult woman whose head is a red strawberry with golden seeds, big brown eyes and green leaves at the crown, human feminine body, black knee-length dress, cream cropped jacket, gold hoop earrings and black shoes. She stands on the floor behind the cart handle with both hands on it. RIGHT: Jade, an adult woman with a blue-purple blueberry head, expressive feminine face, black bob haircut, lilac jacket, blue jeans and dark shoes, standing on the floor beside the cart. Clara and Jade look toward each other; Enzo looks toward Jade. Enzo is the sole cart passenger.\n\nThe complete cast is this single strawberry woman, this single banana man and this single blueberry woman. Wide shared eye-level three-quarter view shows their faces and bodies, basket and cart wheels. Cart fills the lower central foreground. Rich warm hair salon setting with mirrors and glowing bulbs, product shelves, plants, marble surfaces and a glass entrance in the deep background. The salon chairs in the background are empty. Lively expressive cinematic 3D, warm side lighting, tactile materials. One single view, no extra human characters, no collage, no captions. The banana portrait supplies his appearance only; this scene's three-person composition determines all body positions."
artifact = root / 'work/mort-de-honte-revision2-opening-manual-2.png'
record_path = root / 'work/mort-de-honte-revision2-opening-request-2.json'
if not artifact.exists():
    record_path.write_text(json.dumps(request, ensure_ascii=False, indent=2), encoding='utf-8')
    content, metadata = asyncio.run(service._generate_image(request))
    artifact.write_bytes(content)
    (root / 'work/mort-de-honte-revision2-opening-metadata-2.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({'image_path': str(artifact), 'reference_order': ['enzo'], 'registered_in_studio': False}, ensure_ascii=False))
