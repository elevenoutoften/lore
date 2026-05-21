export class LoreError extends Error {
  constructor(public statusCode: number, message: string) {
    super(`Lore API error ${statusCode}: ${message}`);
    this.name = "LoreError";
  }
}
