import { actionsForTarget } from './CanvasContextMenu';

describe('actionsForTarget', () => {
  it('offers the node detail view for nodes and triggers', () => {
    for (const kind of ['node', 'trigger'] as const) {
      const ids = actionsForTarget({ kind, id: 'n_1' }, false).map((a) => a.id);
      expect(ids).toContain('detail');
      expect(ids).toContain('configure');
    }
  });

  it('keeps canvas-level actions free of the node detail entry', () => {
    const background = actionsForTarget({ kind: 'background' }, false).map((a) => a.id);
    expect(background).not.toContain('detail');
    expect(background).toContain('tidy');
  });
});
