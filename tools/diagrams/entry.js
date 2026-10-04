import { parseMermaidToExcalidraw } from "@excalidraw/mermaid-to-excalidraw";
import { convertToExcalidrawElements, exportToSvg } from "@excalidraw/excalidraw";
window.convert = async (src) => {
  const { elements, files } = await parseMermaidToExcalidraw(src, { themeVariables: { fontSize: "16px" } });
  const els = convertToExcalidrawElements(elements);
  const appState = { exportBackground: true, viewBackgroundColor: "#ffffff" };
  const svg = await exportToSvg({ elements: els, appState, files: files || {}, exportPadding: 20 });
  return {
    scene: { type: "excalidraw", version: 2, source: "mermaid-to-excalidraw", elements: els, appState: { viewBackgroundColor: "#ffffff", gridSize: null }, files: files || {} },
    svg: svg.outerHTML,
  };
};
