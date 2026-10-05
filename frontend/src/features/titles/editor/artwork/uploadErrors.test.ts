import { describe, expect, it } from 'vitest';
import { uploadErrorMessage } from './uploadErrors';

describe('uploadErrorMessage', () => {
  it('maps the server statuses to the spec copy', () => {
    expect(uploadErrorMessage(415)).toBe('Lumina accepts JPEG, PNG or WebP images.');
    expect(uploadErrorMessage(415, 'animated_image')).toBe('Animated images are not supported. Upload a still JPEG, PNG or WebP.');
    expect(uploadErrorMessage(413)).toBe('That image is larger than 15 MB.');
    expect(uploadErrorMessage(422)).toBe('That image is larger than 8,000 pixels on a side.');
    expect(uploadErrorMessage(503, 'encoder_busy')).toBe('Lumina is busy with another image. Try again in a few seconds.');
    expect(uploadErrorMessage(429)).toBe('You have uploaded a lot of images this hour. Try again later.');
    expect(uploadErrorMessage(409)).toBe('Someone changed this artwork while you were looking. The latest is shown.');
    expect(uploadErrorMessage(409, 'duplicate')).toBe("That image is already one of this title's backdrops.");
    expect(uploadErrorMessage(408, 'body_timeout')).toBe('The upload stalled. Try again on a steadier connection.');
    expect(uploadErrorMessage(507, 'storage_full')).toBe("Lumina's space for uploaded artwork is full. Remove some uploaded images, then try again.");
    expect(uploadErrorMessage(500)).toBe('Lumina could not upload that image. Try again.');
  });
});
