interface ProductPage<T> {
  total: number;
  items: T[];
}

interface ResolvedProductPage<T> extends ProductPage<T> {
  page: number;
}

function validPage(total: number, pageSize: number, requestedPage: number) {
  if (total <= 0) return 0;
  return Math.min(
    Math.max(0, requestedPage),
    Math.max(0, Math.ceil(total / pageSize) - 1),
  );
}

export async function loadProductPage<T>(
  requestedPage: number,
  pageSize: number,
  load: (page: number) => Promise<ProductPage<T>>,
): Promise<ResolvedProductPage<T>> {
  const first = await load(requestedPage);
  const page = validPage(first.total, pageSize, requestedPage);

  if (page === requestedPage || first.total === 0) {
    return { page, ...first };
  }

  const corrected = await load(page);
  return { page, ...corrected };
}

export function createLatestRequestRunner() {
  let latestRequest = 0;

  return {
    async run<T>(request: () => Promise<T>): Promise<T | undefined> {
      const requestId = ++latestRequest;
      try {
        const result = await request();
        return requestId === latestRequest ? result : undefined;
      } catch (error) {
        if (requestId !== latestRequest) return undefined;
        throw error;
      }
    },
  };
}
