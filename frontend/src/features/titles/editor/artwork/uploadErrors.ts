export const UPLOAD_MAX_BYTES = 15 * 1024 * 1024;

export const DUPLICATE_COPY = "That image is already one of this title's backdrops.";
export const isDuplicate = (error: { status: number; message: string }) => error.status === 409 && error.message === 'duplicate';

/** The choose route shares the artwork bucket with removes and reorders, so its 429 is not about uploads. */
export const CHOOSE_RATE_COPY = 'Too many artwork changes in a minute. Wait a moment, then try again.';

export function uploadErrorMessage(status: number, code?: string): string {
  if (status === 415 && code === 'animated_image') return 'Animated images are not supported. Upload a still JPEG, PNG or WebP.';
  if (status === 415) return 'Lumina accepts JPEG, PNG or WebP images.';
  if (status === 413) return 'That image is larger than 15 MB.';
  if (status === 422) return 'That image is larger than 8,000 pixels on a side.';
  if (status === 503 && code === 'encoder_busy') return 'Lumina is busy with another image. Try again in a few seconds.';
  if (status === 429) return 'You have uploaded a lot of images this hour. Try again later.';
  if (status === 409 && code === 'duplicate') return DUPLICATE_COPY;
  if (status === 408) return 'The upload stalled. Try again on a steadier connection.';
  if (status === 507) return "Lumina's space for uploaded artwork is full. Remove some uploaded images, then try again.";
  if (status === 409) return 'Someone changed this artwork while you were looking. The latest is shown.';
  return 'Lumina could not upload that image. Try again.';
}
