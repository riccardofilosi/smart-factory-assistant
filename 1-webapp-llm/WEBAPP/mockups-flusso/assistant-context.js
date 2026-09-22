(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.AssistantContext = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  // Costruisce il payload che accompagna ogni domanda all'assistente:
  // dice al backend quale commessa è aperta e a che punto è l'operatore.
  function build({ commessa, fase, vistaNavigatore, numeroPassi, esecuzione, idRiserva } = {}) {
    const contesto = {};

    if (typeof commessa === "string" && commessa) contesto.commessa = commessa;
    if (typeof fase === "string" && fase) contesto.fase = fase;

    const indice = vistaNavigatore && vistaNavigatore.currentIndex;
    if (Number.isInteger(indice)) {
      contesto.passo = indice + 1;
      if (Number.isInteger(numeroPassi) && numeroPassi > 0) contesto.passoTot = numeroPassi;
    }

    const idEsecuzione = esecuzione && typeof esecuzione === "object" ? esecuzione.executionId : null;
    const sessione = (typeof idEsecuzione === "string" && idEsecuzione)
      ? idEsecuzione
      : (typeof idRiserva === "string" && idRiserva ? idRiserva : null);

    return { sessione, contesto: Object.keys(contesto).length ? contesto : null };
  }

  return { build };
});
