import { error } from "@sveltejs/kit";
export function load({ params }: { params: { id: string } }) {
  const id = Number(params.id);
  if (!Number.isInteger(id) || id < 1) error(404, "Série introuvable.");
  return { id };
}
