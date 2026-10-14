/** Real EPUB/CBZ import, reading, and personal-library journey against the backend. */
import { createHash } from "node:crypto";
import { strFromU8, unzipSync, zipSync, strToU8 } from "fflate";

import { expect, test } from "./realstack";

// The existing 1×1 PNG fixture used by the browser suite. It is enough to prove a real CBZ page and cover response.
const PIXEL = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkqOcHAAGmAQs+B90oAAAAAElFTkSuQmCC",
  "base64",
);
const epubText = (value: string) => strToU8(value);

function sampleEpub(): Buffer {
  return Buffer.from(
    zipSync({
      mimetype: epubText("application/epub+zip"),
      "META-INF/container.xml": epubText(
        "<?xml version='1.0'?><container><rootfiles><rootfile full-path='OPS/book.opf'/></rootfiles></container>",
      ),
      "OPS/book.opf": epubText(
        "<?xml version='1.0'?><package><metadata xmlns:dc='http://purl.org/dc/elements/1.1/'><dc:title>Realstack Lantern Tales</dc:title><dc:creator>Test Cartographer</dc:creator><dc:description>A safely rendered voyage through a lantern-lit harbor.</dc:description><dc:language>en</dc:language><dc:subject>Adventure</dc:subject><meta name='cover' content='cover'/></metadata><manifest><item id='one' href='one.xhtml' media-type='application/xhtml+xml'/><item id='two' href='two.xhtml' media-type='application/xhtml+xml'/><item id='nav' href='nav.xhtml' properties='nav' media-type='application/xhtml+xml'/><item id='cover' href='cover.png' media-type='image/png'/></manifest><spine><itemref idref='one'/><itemref idref='two'/></spine></package>",
      ),
      "OPS/nav.xhtml": epubText(
        "<html><body><nav><a href='one.xhtml'>The harbor light</a><a href='two.xhtml'>Across the water</a></nav></body></html>",
      ),
      "OPS/one.xhtml": epubText(
        "<html><body><h1>The harbor light</h1><p>A lantern held the tide in its glass.</p><p><a href='two.xhtml#water'>Follow the water</a></p></body></html>",
      ),
      "OPS/two.xhtml": epubText(
        "<html><body><h2 id='water'>Across the water</h2><p>The small boat found the patient morning.</p></body></html>",
      ),
      "OPS/cover.png": PIXEL,
    }),
  );
}

function sampleCbz(): Buffer {
  return Buffer.from(
    zipSync({
      "10.png": PIXEL,
      "2.png": PIXEL,
      "1.png": PIXEL,
      "ComicInfo.xml": epubText(
        "<ComicInfo><Title>Lantern Manga</Title><Writer>Test Cartographer</Writer><Series>Lantern Manga</Series><Number>1</Number><Summary>A right-to-left harbor story.</Summary><Genre>Adventure</Genre><Manga>YesAndRightToLeft</Manga></ComicInfo>",
      ),
    }),
  );
}

async function assertNoHorizontalOverflow(
  page: import("@playwright/test").Page,
) {
  await expect
    .poll(() =>
      page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    )
    .toBe(true);
}

test("Books imports real EPUB and CBZ archives, persists readers, and keeps personal shelves", async ({
  page,
}, testInfo) => {
  test.setTimeout(180_000);
  let epubId = "";
  let mangaId = "";
  const epub = sampleEpub();

  await test.step("the owner imports the generated EPUB and CBZ through Books", async () => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/books");
    await expect(
      page.getByRole("heading", { level: 1, name: "Books" }),
    ).toBeVisible();
    const chooser = page.getByLabel("Choose EPUB, PDF, CBZ, and CBR files");
    await chooser.setInputFiles([
      {
        name: "lantern-tales.epub",
        mimeType: "application/epub+zip",
        buffer: epub,
      },
      {
        name: "lantern-manga.cbz",
        mimeType: "application/vnd.comicbook+zip",
        buffer: sampleCbz(),
      },
    ]);
    const selectedOpf = strFromU8(
      unzipSync(new Uint8Array(epub))["OPS/book.opf"],
    );
    expect(selectedOpf).toContain("<spine>");
    expect(selectedOpf).toContain("idref='one'");
    expect(createHash("sha256").update(epub).digest("hex")).toHaveLength(64);
    const importer = page.getByRole("dialog", { name: "Import into Books" });
    await expect(importer).toContainText("lantern-tales.epub");
    const epubUpload = page.waitForRequest(
      (request) =>
        request.method() === "PUT" &&
        new URL(request.url()).pathname === "/api/books/import" &&
        new URL(request.url()).searchParams.get("filename") ===
          "lantern-tales.epub",
    );
    const epubResponse = page.waitForResponse(
      (response) =>
        response.request().method() === "PUT" &&
        new URL(response.url()).pathname === "/api/books/import" &&
        new URL(response.url()).searchParams.get("filename") ===
          "lantern-tales.epub",
    );
    await importer.getByRole("button", { name: "Import 2 files" }).click();
    const [request, response] = await Promise.all([epubUpload, epubResponse]);
    const responseBody = await response.text();
    await testInfo.attach("book-import-request-url.txt", {
      body: request.url(),
      contentType: "text/plain",
    });
    await testInfo.attach("book-import-response.txt", {
      body: responseBody,
      contentType: "application/json",
    });
    expect(response.status(), responseBody).toBe(201);
    await expect(importer.getByText("Imported")).toHaveCount(2, {
      timeout: 30_000,
    });
    await importer.getByRole("button", { name: "Done" }).click();
    await expect(
      page.getByText("Books imported into your private collection."),
    ).toBeVisible();
    await page.screenshot({
      path: "../output/playwright/realstack/books-desktop.png",
      fullPage: true,
    });

    const listed = (await (
      await page.request.get("/api/books?limit=10")
    ).json()) as { items: { id: string; title: string }[] };
    epubId = listed.items.find(
      (book) => book.title === "Realstack Lantern Tales",
    )!.id;
    mangaId = listed.items.find((book) => book.title === "Lantern Manga")!.id;
    expect(epubId).toBeTruthy();
    expect(mangaId).toBeTruthy();
  });

  await test.step("the EPUB reader shows prose, table of contents, appearance controls, and a saved chapter", async () => {
    await page.reload();
    const epubCard = page
      .getByRole("button", { name: "Open Realstack Lantern Tales" })
      .first();
    await expect(epubCard).toBeVisible();
    await epubCard.click();
    await expect(
      page.getByRole("heading", { level: 1, name: "Realstack Lantern Tales" }),
    ).toBeVisible();
    await page.getByRole("button", { name: "Add to favorites" }).click();
    await expect(
      page.getByRole("button", { name: "Remove from favorites" }),
    ).toBeVisible();
    await page.getByRole("button", { name: "Add to shelf" }).click();
    await page
      .getByRole("dialog", { name: "Add to shelf" })
      .getByRole("button", { name: "Create a shelf" })
      .click();
    const shelf = page.getByRole("dialog", { name: "Create a shelf" });
    await shelf.getByLabel("Shelf name").fill("Harbor evenings");
    await shelf.getByRole("button", { name: "Create shelf" }).click();
    const addToShelf = page.getByRole("dialog", { name: "Add to shelf" });
    await addToShelf.getByRole("checkbox", { name: "Harbor evenings" }).check();
    await addToShelf.getByRole("button", { name: "Add to shelves" }).click();
    await expect(page.getByText("Added to your shelves.")).toBeVisible();

    await page.getByRole("button", { name: "Start reading" }).click();
    await expect(page).toHaveURL(new RegExp(`/books/read/${epubId}$`));
    await expect(page.getByLabel("Book chapter")).toContainText(
      "A lantern held the tide in its glass.",
    );
    const chapters = page.getByRole("button", { name: "Chapters" });
    if ((await chapters.getAttribute("aria-expanded")) === "false")
      await chapters.click();
    await page.getByRole("button", { name: /Across the water/ }).click();
    await expect(page.getByLabel("Book chapter")).toContainText(
      "The small boat found the patient morning.",
    );
    await page.getByRole("button", { name: "Reading appearance" }).click();
    await page
      .getByLabel("Reading appearance")
      .getByRole("button", { name: "Larger text" })
      .click();
    await expect(
      page.getByLabel("Reading appearance").getByText("20px"),
    ).toBeVisible();
    await page.screenshot({
      path: "../output/playwright/realstack/books-epub-reader.png",
      fullPage: true,
    });
    await expect
      .poll(
        async () =>
          (
            (await (await page.request.get(`/api/books/${epubId}`)).json()) as {
              progress: { unit_index: number };
            }
          ).progress.unit_index,
      )
      .toBe(1);
    await page.reload();
    await expect(page.getByLabel("Book chapter")).toContainText(
      "The small boat found the patient morning.",
    );
  });

  await test.step("the manga series reads naturally ordered pages and restores its position", async () => {
    await page.goto("/books/manga");
    await page
      .getByRole("button", { name: /Open Lantern Manga, 1 volumes?/ })
      .click();
    await expect(
      page.getByRole("heading", { level: 1, name: "Lantern Manga" }),
    ).toBeVisible();
    await expect(page.getByText("Right to left")).toBeVisible();
    await page.getByRole("button", { name: "Read volume 1" }).click();
    await expect(page).toHaveURL(new RegExp(`/books/read/${mangaId}$`));
    await expect(page.getByAltText("Page 1 of 3")).toBeVisible();
    await page.getByRole("button", { name: "Next" }).click();
    await expect(page.getByAltText("Page 2 of 3")).toBeVisible();
    await expect
      .poll(
        async () =>
          (
            (await (
              await page.request.get(`/api/books/${mangaId}`)
            ).json()) as { progress: { unit_index: number } }
          ).progress.unit_index,
      )
      .toBe(1);
    await page.reload();
    await expect(page.getByAltText("Page 2 of 3")).toBeVisible();
  });

  await test.step("Books remains an isolated, responsive surface on a phone viewport", async () => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/books");
    await expect(
      page.getByRole("heading", { level: 1, name: "Books" }),
    ).toBeVisible();
    await assertNoHorizontalOverflow(page);
    await page.screenshot({
      path: "../output/playwright/realstack/books-phone.png",
      fullPage: true,
    });
    await page.goBack();
    await expect(page).toHaveURL(/\/books\/read\//);
    await page.goto("/books");
    await expect(
      page.getByRole("heading", { level: 1, name: "Books" }),
    ).toBeVisible();
  });
});
