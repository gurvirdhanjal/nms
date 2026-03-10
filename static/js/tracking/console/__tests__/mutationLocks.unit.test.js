import { describe, expect, it } from 'vitest';
import { createRequire } from 'module';

const require = createRequire(import.meta.url);
const mutationLocks = require('../mutationLocks.js');

describe('mutationLocks', () => {
  it('locks and unlocks actions', () => {
    const locks = mutationLocks.createMutationLocks();
    locks.lock('policy:add');
    expect(locks.isLocked('policy:add')).toBe(true);
    locks.unlock('policy:add');
    expect(locks.isLocked('policy:add')).toBe(false);
  });

  it('prevents concurrent action execution', async () => {
    const locks = mutationLocks.createMutationLocks();
    const first = locks.withLock('k', async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
      return 'ok';
    });

    await expect(locks.withLock('k', async () => 'second')).rejects.toMatchObject({ code: 'LOCKED' });
    await expect(first).resolves.toBe('ok');
  });
});
