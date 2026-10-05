/** Roda um handler de api/_handlers/<nome>.js em memória e devolve o JSON (ou null se falhar). req.perfil já deve estar definido. */
function executarHandler(nome, req) {
  return new Promise((resolve) => {
    const res = {
      statusCode: 200,
      setHeader() {},
      status(codigo) { this.statusCode = codigo; return this; },
      json(corpo) { resolve(this.statusCode < 400 ? corpo : null); return this; }
    };
    Promise.resolve(require(`./_handlers/${nome}`)(req, res)).then(() => resolve(null), () => resolve(null));
  });
}

module.exports = { executarHandler };
